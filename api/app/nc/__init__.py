"""Nextcloud-compatibiliteit: de officiële Nextcloud-clients (desktop, Android, iOS) en andere
WebDAV-clients (rclone, Finder, Verkenner via een hulpprogramma) kunnen met het platform
synchroniseren. Eigen code; geen Nextcloud of SabreDAV erin.

- auth.py: Basic-auth met app-wachtwoorden (2FA blijft voor de web-app en /api/v1).
- etags.py: de ETag van een map verandert als er ergens eronder iets verandert; daarop leunt
  de sync van de desktopclient.
- routers/nc_ocs.py: status.php, inlogflow v2, capabilities, gebruiker, app-wachtwoorden.
- routers/dav.py: WebDAV onder /remote.php/dav/files/<login>/ en /remote.php/webdav/.
"""
