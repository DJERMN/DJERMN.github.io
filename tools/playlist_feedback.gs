// Store customer song exclusions in a private Google Sheet.
//
// Setup:
// 1. Create a standalone project at https://script.google.com/ and paste this file.
// 2. Run setup() once and authorize access to Sheets and email.
// 3. Deploy as a Web app: execute as yourself; allow access to anyone.
// 4. Pass the deployed /exec URL to build_playlist_page.py with --feedback-endpoint.

const SPREADSHEET_NAME = 'DJERMN – Kunden-Songwünsche';
const SHEET_NAME = 'Einsendungen';
const ALLOWED_ORIGINS = ['https://djermn.github.io'];
const MAX_SONGS_PER_SUBMISSION = 250;
const NOTIFY_EMAIL = 'info@djerman.de';

function setup() {
  const properties = PropertiesService.getScriptProperties();
  let spreadsheetId = properties.getProperty('SPREADSHEET_ID');
  let spreadsheet;

  if (spreadsheetId) {
    spreadsheet = SpreadsheetApp.openById(spreadsheetId);
  } else {
    spreadsheet = SpreadsheetApp.create(SPREADSHEET_NAME);
    spreadsheetId = spreadsheet.getId();
    properties.setProperty('SPREADSHEET_ID', spreadsheetId);
  }

  let sheet = spreadsheet.getSheetByName(SHEET_NAME);
  if (!sheet) {
    const sheets = spreadsheet.getSheets();
    if (sheets.length === 1 && sheets[0].getLastRow() === 0) {
      sheet = sheets[0];
      sheet.setName(SHEET_NAME);
    } else {
      sheet = spreadsheet.insertSheet(SHEET_NAME);
    }
  }
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(['Empfangen am', 'Veranstaltung', 'Einsendung', 'Titel', 'Artist', 'Sparte']);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, 6).setFontWeight('bold');
  }

  properties.setProperty('NOTIFY_EMAIL', NOTIFY_EMAIL);

  Logger.log('Google Sheet: ' + spreadsheet.getUrl());
  Logger.log('E-Mail-Benachrichtigung: ' + (properties.getProperty('NOTIFY_EMAIL') || 'aus'));
}

function doPost(event) {
  let payload = {};
  let submissionId = '';
  let origin = '';

  try {
    const rawPayload = event && event.parameter && event.parameter.payload;
    if (!rawPayload) throw new Error('Leere Einsendung');
    payload = JSON.parse(rawPayload);
    submissionId = cleanText_(payload.submissionId, 100);
    origin = cleanText_(payload.origin, 300);

    if (ALLOWED_ORIGINS.indexOf(origin) === -1) throw new Error('Unbekannte Website');
    if (cleanText_(payload.website, 300)) throw new Error('Automatische Einsendung blockiert');

    const eventName = cleanText_(payload.event, 160);
    if (!eventName || !submissionId) throw new Error('Veranstaltung oder Einsendungs-ID fehlt');
    if (!Array.isArray(payload.songs) || payload.songs.length === 0 ||
        payload.songs.length > MAX_SONGS_PER_SUBMISSION) {
      throw new Error('Ungültige Anzahl an Titeln');
    }

    const songs = payload.songs.map(song => ({
      title: cleanText_(song && song.song, 240),
      artist: cleanText_(song && song.artist, 200),
      playlist: cleanText_(song && song.playlist, 200)
    }));
    if (songs.some(song => !song.title || !song.artist || !song.playlist || /^[\s\-–—]+$/.test(song.title))) {
      throw new Error('Ungültiger Titel in der Einsendung');
    }

    const properties = PropertiesService.getScriptProperties();
    const spreadsheetId = properties.getProperty('SPREADSHEET_ID');
    if (!spreadsheetId) throw new Error('Google Sheet wurde noch nicht eingerichtet');

    const lock = LockService.getScriptLock();
    lock.waitLock(10000);
    try {
      const spreadsheet = SpreadsheetApp.openById(spreadsheetId);
      const sheet = spreadsheet.getSheetByName(SHEET_NAME);
      if (!sheet) throw new Error('Einsendungen-Tab fehlt');
      const receivedAt = new Date();
      const rows = songs.map(song => [
        receivedAt,
        safeCell_(eventName),
        submissionId,
        safeCell_(song.title),
        safeCell_(song.artist),
        safeCell_(song.playlist)
      ]);
      sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, 6).setValues(rows);
    } finally {
      lock.releaseLock();
    }

    notifyOwner_(eventName, songs, submissionId);
    return responseHtml_({ ok: true, submissionId: submissionId }, origin);
  } catch (error) {
    console.error('Einsendung fehlgeschlagen: ' + error.message);
    return responseHtml_({ ok: false, submissionId: submissionId }, origin);
  }
}

function notifyOwner_(eventName, songs, submissionId) {
  const email = PropertiesService.getScriptProperties().getProperty('NOTIFY_EMAIL');
  if (!email) return;

  const body = [
    'Veranstaltung: ' + eventName,
    'Einsendung: ' + submissionId,
    '',
    'Diese Songs bitte weglassen:',
    ...songs.map(song => song.playlist + ': ' + song.title + ' — ' + song.artist)
  ].join('\n');

  try {
    MailApp.sendEmail(email, 'Song-Ausschlussliste: ' + eventName, body);
  } catch (error) {
    // The Sheet write is already complete; an email failure must not reject the submission.
    console.error('E-Mail-Benachrichtigung fehlgeschlagen: ' + error.message);
  }
}

function responseHtml_(result, origin) {
  const targetOrigin = ALLOWED_ORIGINS.indexOf(origin) !== -1 ? origin : ALLOWED_ORIGINS[0];
  const message = JSON.stringify({
    type: 'playlist-feedback-result',
    ok: result.ok,
    submissionId: result.submissionId
  }).replace(/</g, '\\u003c');
  const target = JSON.stringify(targetOrigin);
  const html = '<!doctype html><html><head><meta charset="utf-8"></head><body>' +
    '<script>window.top.postMessage(' + message + ',' + target + ');</' + 'script>' +
    '</body></html>';

  return HtmlService.createHtmlOutput(html)
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function cleanText_(value, maxLength) {
  return String(value == null ? '' : value)
    .replace(/[\u0000-\u001F\u007F]/g, ' ')
    .trim()
    .slice(0, maxLength);
}

function safeCell_(value) {
  return /^[=+\-@]/.test(value) ? "'" + value : value;
}
