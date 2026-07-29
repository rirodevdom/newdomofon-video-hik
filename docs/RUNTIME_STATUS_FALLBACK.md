# Runtime fallback for Hikvision channel status

Some Hikvision recorders expose streaming channels but do not publish a usable online/offline value through the InputProxy status endpoints.

The node keeps an explicit ISAPI status when it is present. When ISAPI returns no status (`null`), the public snapshot uses the live recorder runtime:

- running recorder -> `online=true`;
- recorder with restart/error -> `online=false`;
- recorder not started and no error -> `online=null`.

The persisted ISAPI discovery data is not overwritten; the fallback is applied only when returning control API data and when reporting discovery to master.
