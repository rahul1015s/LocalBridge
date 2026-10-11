# License: free to use, modify and share

LocalBridge is licensed under the **GNU General Public License, version 3 or (at your option) any
later version** (GPL-3.0-or-later). The legal text is in [../LICENSE](../LICENSE); this page is a
plain-language summary, not legal advice.

## You may
* **Use** the app for any purpose, personal or commercial, on any number of devices. It costs nothing.
* **Study and modify** it. The complete source code is provided.
* **Share** it, with or without changes, for free or for a fee.

## You must (when you distribute it or a modified version)
* Keep it under the **same GPL license** and include the license text.
* Provide the **source code** of your version to the people who receive it.
* Keep existing copyright and license notices, and mark your changes.

## You may not
* Make it proprietary or add restrictions that take these freedoms away from your users.
* Claim it is yours or imply the original authors endorse your version.

## Other things worth knowing
* **No warranty.** The software is provided "as is", without guarantee of any kind (GPL sections 15-16).
  It has not been independently security-audited.
* **No data collection.** The app has no analytics, ads, accounts or cloud service. Files go directly
  between your own devices.
* **Dependencies** keep their own (compatible) licenses: Python/FastAPI/uvicorn/cryptography/qrcode/
  zeroconf/psutil on the desktop; Kotlin, AndroidX/Compose, OkHttp, ZXing on Android (Apache-2.0/MIT/BSD).
  Run `pip-licenses` / Gradle license reports before a public release to produce a full list.
* **Name.** The code is free; the name "LocalBridge" is not yet checked for trademark conflicts, so a
  fork may want to choose its own name.
* **Contributing.** Contributions are accepted under the same license (inbound = outbound).

## Adding the notice to new source files
```
LocalBridge: private local-network file transfer.
Copyright (C) <year> <contributors>
Licensed under the GNU GPL v3 or later; see the LICENSE file. No warranty.
```
