# Google-Sheets-Einsendungen für Playlist-Seiten

Das Apps-Script-Backend steht in `playlist_feedback.gs`. Es speichert pro
ausgewähltem Titel eine Zeile in einem privaten Google Sheet und sendet eine
Benachrichtigung an `info@djerman.de`.

## Einmalige Einrichtung

1. Unter [script.google.com](https://script.google.com/) ein neues Projekt erstellen.
2. Den Inhalt von `playlist_feedback.gs` in `Code.gs` einfügen und speichern.
3. `setup()` auswählen, ausführen und die angefragten Google-Berechtigungen erteilen.
   Die Ausführungsprotokolle enthalten den Link zum erstellten Sheet. Das Setup
   setzt `NOTIFY_EMAIL` auf `info@djerman.de`.
4. **Bereitstellen → Neue Bereitstellung → Web-App** auswählen:
   - Ausführen als: **Ich**
   - Zugriff: **Jeder**
5. Die erzeugte Web-App-URL mit `/exec` kopieren.
6. Die Seite mit dieser URL aktualisieren, zum Beispiel:

   ```bash
   python3 tools/build_playlist_page.py \
     --folder "Geburtstag Lucas & Sarah" \
      --output p/lukas-sarah/index.html \
     --title "Geburtstag Lukas & Sarah — Playlists" \
     --feedback-endpoint "https://script.google.com/macros/s/DEPLOYMENT_ID/exec"
   ```

Der Generator übernimmt eine bereits gespeicherte Endpoint-URL bei späteren
Updates. Die Einsendung wird erst als erfolgreich bestätigt, wenn das Apps
Script die ausgewählten Titel tatsächlich in das Sheet geschrieben hat.
Bei Fehler oder ausbleibender Bestätigung bleibt die Auswahl erhalten und die
Seite bietet das manuelle Teilen via WhatsApp an.

Das Web-App-Deployment muss von `https://djermn.github.io` erreichbar sein.
Das Sheet selbst bleibt privat; Kunden erhalten nur Zugriff auf das
Einsendeformular, nicht auf die Tabelle.

## Bereits bereitgestelltes Script

Bei einer bestehenden Bereitstellung die Benachrichtigungsadresse unter
**Projekteinstellungen → Script Properties** auf `NOTIFY_EMAIL = info@djerman.de`
setzen. Die laufende Web-App liest diese Property pro Einsendung; eine neue
Bereitstellung ist dafür nicht nötig.
