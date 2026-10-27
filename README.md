# Instagram Local for Windows

A personal Instagram downloader with a Python backend and a compact, English, dark browser interface. It binds only to `127.0.0.1`. No paid API, remote backend, downloader service, hosting, or cloud deployment is involved.

## Version 2: offline Library

**Downloader remains the opening tab.** The new **Library** tab shows a responsive grid of local downloads, with no home feed. Switching tabs keeps the downloader's unsaved fields and file selection in place and does not pause or restart its worker. A previously running download queue continues independently while you browse.

The Library works without Internet access or a valid Instagram session. Searching, filtering, favoriting, hiding/restoring, thumbnails, and media playback use this local server and local files only. Keep the app's server running; this is not a remotely hosted or browser-cached site. All image/video/script/style sources are local. The original Instagram link opens an external page only if you choose to click it.

- **Grid:** newest downloaded first, up to 48 entries per page, with lazy thumbnail loading. Search literal text from the download date (`YYYY-MM-DD`), canonical post link/shortcode, or output filename. Account filters use only known usernames. Favorites and known source/collection labels have separate filters. Use Refresh to include newly indexed/downloaded posts.
- **Viewer:** select a tile for an image/video viewer, caption and known metadata. A numbered carousel slider reads ZIP members in numeric order. Left/right arrows move between media, up/down move between posts; native video and slider controls retain their own keyboard behavior. Previous/next post buttons also cross page boundaries. Back to grid or Escape returns to the grid and its scroll position. Videos use standard browser controls, without auto-playing.
- **Favorites:** stored only in the local database. They do not change Instagram Saved posts. **Hide from library** keeps media, history, and duplicate detection; enable **Hidden items**, open the entry, then **Restore to library** to show it again.
- **Delete local file…:** a separate confirmation lists the exact recorded output and recorded caption sidecar, including their full paths. Only those files and that entry's thumbnails are removed. This deletion is permanent, not a Recycle Bin action. It leaves download history intact and marks the Library entry deleted. Missing output then permits a future download through normal duplicate handling. Unrecorded nearby text files are never included in deletion.
- **Fetch missing details…:** appears only when a caption or account name is missing and a reliable post identity is available. Its confirmation clearly identifies it as an online action that may use your imported session. It adds one metadata-only task to the existing Downloader queue; no media is downloaded again. Resume in Downloader if paused; an already running queue can process it in the current batch. It follows the same pacing, retry limits, and account-restriction pauses. It never runs when opening, searching, filtering, or viewing the Library. Refresh after the task finishes to see its results. Saved timestamps, collection membership, and profile details are not fetched or invented.

### Safe migration of existing downloads

On startup, version 2 adds `library_*` SQLite tables without replacing version 1 tables. A background, database-only migration registers completed paths from `downloads`, plus older completed output paths still recorded in queue history. This includes different user-selected download folders. It does not scan arbitrary folders, move files, change sessions/settings, resume the network queue, or download anything. Batches and import markers commit transactionally; restarting safely continues unfinished migration. Local file inspection follows incrementally, and thumbnails are generated on demand for visible entries.

Older records have limited metadata. A username and local date can be established from a matching version 1 filename. If there is no matching filename date but a timezone-aware timestamp exists in the record, the date is displayed in the machine's current local timezone. Canonical links derive from recorded shortcodes. Existing `.txt` sidecars and `caption.txt` in ZIPs supply captions where available. Unknown values are explicitly shown as unknown; no saved dates, collection names, or account profile data are inferred. Source labels for old rows remain unknown unless they were recorded. Each recorded output is a separate Library entry, so explicit repeat downloads may appear separately.

Future download completion saves shortcode, username, caption, canonical link, download time/date, media count/types, output path, and known input source/collection label in the same SQLite transaction as success. **Caption metadata is saved even when caption text-file output is disabled.** Signed CDN URLs/manifests and session material are not included. A Library details-fetch task updates missing metadata only and never marks a media download complete.

Missing/moved outputs stay visible with a missing status and recorded path. Restore the file to that path and Refresh, or download the post again. Damaged/unsupported ZIPs and incomplete numbering have visible errors; staging paths under `.incomplete` are never playable Library posts. Original files are not damaged by inspection or preview failure. Header and ZIP structure checks are lightweight; a damaged body or unsupported browser codec can still fail at playback, which is reported in the viewer. Full stored-ZIP reads also check the member CRC. Compressed ZIP seeking is supported but can be slower because decompression must pass earlier bytes; version 1's default uncompressed ZIPs use direct byte slices for video seeking. No extracted carousel folders are left behind.

### Local previews and media access

The already bundled FFmpeg creates small JPEG previews, including videos and the first media item in a carousel. Its remote protocols are disabled. ZIP previews stream from the member without creating an extracted media directory; some non-seekable MP4 layouts may fail to preview and show **No preview** while the original can still play normally. Cache files live in `.local/thumbnails`, with a 128 MiB / 1,000-file budget, whichever is reached first, and oldest-accessed entries pruned. A single in-progress preview can temporarily add a file; stale partial previews are cleaned. Previews invalidate when source size, modification time, or file identity changes. Failed previews are cached as placeholders until the source changes or that cached failure marker is removed with the server stopped.

Images, videos, and ZIP members are streamed through authenticated routes using Library IDs and media positions, never client-supplied paths. Video routes support single HTTP byte ranges and HEAD. An authenticated local action establishes a per-run, HttpOnly, SameSite=Strict media cookie; media requests additionally require the exact loopback Host and same-origin browser request metadata. Cross-origin pages cannot read them. CSP permits only local images/media. Symlinks, junctions, non-local paths, traversal entries, and staging paths are refused. These controls retain the local-machine security limitations described below.

## Start

**Upgrading from version 1:** if its server is running, use **Shut down** first, then run **Launch.cmd**. Keep `.local`, your existing environment, and all download folders. The Library migration runs automatically; no new packages or fresh account setup are required.

1. Double-click **Setup.cmd** once. It creates `.venv`, installs the exact versions in `requirements.txt`, restricts `.local` to your Windows user and SYSTEM, and runs the local fixture tests. Python 3.11+ is required (developed with Python 3.13 on Windows).
2. Double-click **Launch.cmd**. Keep the console window open. The browser opens after the local server is ready.
3. Open **Connection & settings**, choose an absolute download folder, and save it. The default is this project's `Downloads` folder. Settings affect newly queued work; existing items retain their original folder/caption choices.
4. Add links, import a TXT/CSV file, or queue a profile/Saved source. Choose **Resume · next 50** to begin.

After running Setup once in this folder, use `Launch.cmd` for later starts. Run Setup again if the project moves to another folder or computer: Python virtual environments are not portable.

**Pause** takes effect after the current post or enumeration request. **Cancel remaining** cancels pending work and keeps completed files. A batch pauses after 50 processed posts; review results and Resume for the next batch. Already-downloaded entries also count toward this cautious processing limit. A source scan with 50 already-downloaded entries pauses for review too.

Closing the browser leaves the server running. **Shut down** in the interface, or Ctrl+C in its console, closes it. A transfer interrupted by shutdown may remain incomplete. After restarting, the queue is paused and needs manual Resume. Completed media items from an interrupted carousel are reused where possible.

Launching again reopens the existing instance. Port 8765 is preferred; an occupied port causes selection of the next free port through 8795. The app verifies an existing instance before reopening it. Do not open a bare localhost URL manually: Launch supplies a temporary authorization capability, then removes it from the address bar.

## Authentication without an Instagram password

Public links are attempted without cookies. Instagram may still require login for a public post. Private-followed profiles and Saved access require a session permitted to view that content.

1. Connect your VPN/TUN or working proxy, open Instagram in Chrome, and log in normally. Resolve any checkpoint there yourself.
2. Export **only instagram.com cookies** to a Netscape `cookies.txt` or browser cookie JSON export. Use a locally installed, trusted cookie-export tool you have inspected; exports must include HTTP-only `sessionid`. An ordinary `document.cookie` copy is insufficient because it omits HTTP-only cookies. JSON entries need `domain`, `name`, `value`, and optionally `expirationDate`/`expires`. Netscape exports must retain their seven tab-separated fields and any `#HttpOnly_` prefixes.
3. In **Connection & settings → Instagram session**, choose the export and select **Import cookies**. The app parses it in memory and saves an encrypted copy. It does not copy the plaintext export into project state.
4. Choose **Verify Saved access** to request at most the first Saved page, without downloading media. This is a real authenticated check; merely importing cookies is not proof of a valid session.
5. Remove the plaintext export after importing if you no longer need it. The app cannot remove the original browser export for you. Never paste or send it to a website or into a support message.

You can make a JSON file manually without an extension. In Chrome, open Instagram while signed in, press F12, then select **Application → Storage → Cookies → https://www.instagram.com**. Copy the raw **Value** and **Domain** for `sessionid`, `csrftoken`, and `ds_user_id` if present. Leave **Show URL-decoded** off. In Notepad, save a file such as `instagram-cookies.json` containing a JSON array of entries like `{"domain":".instagram.com","name":"sessionid","value":"PASTE_VALUE_HERE"}`; use each cookie's actual domain and value. Choose **Save as type: All files** so the filename ends in `.json`. Import and verify it as above. This file grants account access: keep it private, never commit it to a repository, and remove it when no longer needed.

Chrome cookie encryption on current Windows builds can prevent automatic browser extraction. This app deliberately does not attempt it or collect a password. If your browser/export tool cannot export HTTP-only Instagram cookies, session import cannot work; use a trusted local exporter that supports them, or continue with public links where Instagram permits anonymous access.

**Clear session** removes this app's imported cookies and resets verification status. It does not log Chrome out. To invalidate a session that may have escaped your control, use Instagram's own login/session controls.

## Download inputs

- **Links:** one full post/Reel URL per line, with either HTTP or HTTPS and optional tracking parameters. URLs normalize to HTTPS and deduplicate by shortcode across all sources. Blank lines are ignored. A bad line does not reject the other lines. `/p/`, `/reel/`, `/reels/`, and legacy `/tv/` post links are accepted. `/share/` redirects, Stories, Highlights, and live/archive links are outside this input format; open a shared post in Instagram and copy its canonical post/Reel URL.
- **File import:** UTF-8 TXT or CSV, up to 4 MB and 20,000 links per request. Comma, semicolon, and tab-separated CSV are recognized. An unambiguous URL/link column is selected automatically. Multiple link-containing columns require a selection. CSV imports preserve malformed values for individual error reports. Files may contain a header or be a single URL column without a header.
- **Profile:** username or profile URL, Latest N or All. Both the timeline and Reels tab are scanned incrementally. Latest N combines publication times from the two streams, including relevant pinned posts, and deduplicates identities. It assumes each stream is otherwise recent-first, as returned by the engine; malformed/missing publication data stops enumeration visibly instead of guessing. Large Latest N scans keep candidates in SQLite. A resume after 50 scanned candidates may be needed before the latest list is ready to download.
- **Saved:** All Saved posts or a named collection, Latest N or All. Authentication is required. To select a collection, open it in Instagram and copy a URL like `https://www.instagram.com/you/saved/name/123456/`. The name is shown in the queue. The engine supports collection contents but has no maintained collection-discovery method; therefore there is no automatic list of collection names. If Instagram does not expose a usable name/ID URL, direct selection of that collection is unavailable. Concrete fallback: copy the individual post/Reel links from that collection and queue them in Links or a TXT file.

### Saved order is explicit

The selected engine does not document a reliable recently-saved ordering contract or per-item saved timestamps. **Latest N in available order** therefore requires an unchecked-by-default acceptance box. Returned order is preserved. The app never sorts Saved by publication date or invents saved timestamps. All items requires no ordering acceptance.

If Instagram blocks the Saved or collection endpoint, direct library access cannot be claimed as working. The error is shown; pasting Saved-post links remains useful but downloads only those linked posts. It does not enumerate the Saved library.

Instagram libraries can change while paused. Cursors plus offsets resume within the last page; edits to the library can shift entries. If a cursor expires or a page changes incompatibly, cancel the source and add it again. Existing successful files will be skipped. This does not guarantee a snapshot of a changing library.

## Files and quality

Base filename: `username-shortcode-YYYY-MM-DD`, where the date is the computer's **local download date**, fixed when the first media attempt is staged. It is not publication time. Windows-reserved characters/names are sanitized. Filename collisions use ` (2)`, ` (3)`, etc.; unrelated output is never intentionally overwritten.

A single image/video becomes its normal media file. A carousel becomes **one ZIP**, with `001.jpg`, `002.mp4`, etc. in carousel order. No duplicate extracted media folder is retained after success. Enable **Save caption and original link** for a small `.txt` alongside single media, or `caption.txt` inside a carousel ZIP.

Images use the largest exposed candidate. Video uses the best usable DASH video plus audio when available, merged by bundled FFmpeg without re-encoding; otherwise it uses the engine's best pre-merged video. There are no artificial quality choices. Available quality may be lower than the original Instagram upload. If the best exposed transfer/merge fails, the post fails visibly instead of silently substituting a thumbnail or lower-quality file.

Downloads are written through temporary files. Incomplete work lives under the selected folder's `.incomplete/<queue-id>/`, with an explicit `INCOMPLETE.txt` marker. It is never a completed download. Completed staged media has an identity, size, and SHA-256 receipt for retry reuse. Successful finalization removes its staging folder. Canceled partials are retained for inspection; with the server stopped, you may delete unwanted `.incomplete` subfolders. Their contents are personal downloaded media, not encrypted session storage.

SQLite records successful shortcodes and output paths. A missing output (or requested caption sidecar) automatically permits re-download. **Download again** overrides completed-history skipping for new work. A post already active in the queue is still deduplicated. Failed posts are not considered complete. **Retry failed** queues failures and failed enumeration sources again, then waits for manual Resume.

## Connection and restrictions

An existing VPN/TUN normally works without app settings. The adapter uses Requests with TLS verification enabled. The app explicitly resolves Python's environment proxies or static Windows Internet Settings (`urllib.request.getproxies`), then supplies that mapping to both extraction and media download. It disables Requests' implicit environment handling to avoid `.netrc` credentials and inconsistent proxy resolution. Python's Windows implementation uses environment settings first, then registry static settings. PAC/WPAD scripts and automatic proxy discovery are not executed. Windows proxy bypass lists are not applied by this explicit mapping; local server requests always bypass proxies.

If needed, enter `http://127.0.0.1:PORT` or `socks5h://127.0.0.1:PORT`. HTTP, HTTPS, SOCKS5 and SOCKS5h are supported. Use SOCKS5h to resolve DNS at the proxy. URL-encode embedded proxy credentials. The explicit address is protected in the DPAPI vault and never echoed back into the interface. **Clear explicit proxy** restores detected system/environment settings or direct VPN/TUN access. **Test connection** checks Instagram reachability; it does not verify login or each CDN path. Pause the queue and let the current post finish before connection/session changes or checks.

The app uses one worker, one post at a time, at least six seconds between extraction requests across posts, and at most three attempts for a transient post failure (5- and 15-second backoffs in addition to request pacing). Enumeration requests also have limited retries. Failed ordinary posts are recorded and the batch continues. Authentication, checkpoint/challenge, access restrictions, and rate limits pause work immediately. Retry-After is respected where supplied and blocks early Resume. Without server guidance, the app leaves the wait to your judgment and Instagram's instructions; it never automatically resumes a restriction. It does not rotate accounts, solve challenges, or promise protection against restrictions.

## Local security

Cookies/proxy credentials are encrypted using current-user Windows DPAPI. `.local` inherits no broad folder permissions; access is granted to the current Windows SID and SYSTEM. SQLite contains local history, usernames, links with tracking parameters stripped, queue settings, and paths, but no cookies, signed CDN links, or FB tokens. Engine disk caches and library diagnostics are disabled. There is no operational log file or remote telemetry; the console prints only fixed startup/shutdown guidance.

The server binds only to IPv4 loopback, validates Host and Origin, requires a random per-run capability on every API action, rejects cross-origin preflight, and applies a restrictive content security policy. The capability travels once in a URL fragment and then stays in tab session storage. Runtime discovery is DPAPI-encrypted and existing-server verification uses a nonce/HMAC. Downloads and captions are local plaintext files. Your account, Chrome extensions, malware running as your Windows user, an administrator, and an unlocked computer remain outside these protections. Treat cookie exports and your Windows login as sensitive; account compromise cannot be ruled out. Keep this project in a private, non-synced folder if local history/media must not be synced by a separate backup/sync tool.

## Troubleshooting

| Problem | Action |
| --- | --- |
| Setup cannot install packages | Check Python 3.11+, your connection to PyPI, and antivirus restrictions. Re-run Setup. No administrator installation is required. |
| Startup fails | Re-run Setup from a writable local folder. Windows DPAPI, `whoami`, and `icacls` must work for the same signed-in Windows account. Don't run the launcher as a different user. |
| Unauthorized local action | Reopen using Launch.cmd; a copied bare URL lacks the per-run capability. |
| Connection fails | Verify Instagram in Chrome through your VPN; try an explicit local proxy port. Browser-only proxy extensions do not configure Python. Don't disable TLS verification. |
| Session expired or challenge | Pause, resolve it in Instagram in Chrome, import fresh cookies, then Resume. Never repeatedly retry the restricted endpoint. |
| Media/ZIP failed | Check free space and output-folder permissions. Retry failed reuses completed staged items. A failed ZIP is never shown as complete. |
| Unexpected Instagram format / stale cursor | Stop that source. Verify Instagram access in Chrome. An engine update may be needed. If only the cursor is stale, cancel and add the source again. |
| Source scans but no posts appear yet | Latest N profile selection is collecting timeline/Reels candidates in SQLite. Resume the next cautious scan batch if prompted. |
| App says it is already starting | Wait briefly and launch again. If the console is gone, a new launch can recover once Windows releases the old process's file lock. Do not delete a lock to force parallel instances. |

## Verification and maintenance

Run repeatable local checks without Instagram access:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check instagram_local/static/app.js
node --check instagram_local/static/library.js
.\.venv\Scripts\python.exe -m pip check
```

The suite covers input normalization/deduplication, CSV ambiguity, Windows filenames, mixed ZIP order/captions, partial reuse, collision handling, missing-file history, persistent recovery, incremental enumeration/cursors, pinned profile selection, retry limits/restriction pauses, redaction, DPAPI, HTTP access protections, and launcher behavior. Version 2 adds migration from version 1 tables, repeatable batches, missing metadata, future caption/source indexing, local details tasks, literal search, persistent Favorites/hidden state, safe deletion, corrupt/staging/traversal rejection, thumbnail invalidation/bounds, media authorization, loose/ZIP video ranges, and 5,000-record bounded pagination. Tests generate tiny video/audio fixtures and run the real yt-dlp/FFmpeg DASH selection/merge pipeline with in-memory HTTP responses. Windows DPAPI/launcher tests need normal Windows user access and can fail in a restricted agent sandbox.

**Live Instagram remains unverified in this checkout:** no session or sample links were supplied. After importing a session, use this small smoke test:

1. Test connection, then Verify Saved access (one Saved page).
2. Queue six user-selected links covering an image, Reel, ordinary video, image carousel, and mixed carousel. Resume once. Inspect the outputs, audio, ZIP order, and optional captions.
3. Queue the same links again; verify skipping. Remove one output and queue its link; verify automatic re-download.
4. Queue a profile Latest 2, Saved Latest 2 after accepting available order, and a named collection Latest 2. Check they match the currently accessible source items/order. Use a followed private-profile link too if that access needs validation.
5. Pause after a post, shut down, relaunch, confirm no automatic requests, then manually Resume.

Do not use a large library or repeated restricted requests as a test. Fixture passing establishes application behavior, not that Instagram's private endpoints currently work for your account/region/session. See [ENGINE_RESEARCH.md](ENGINE_RESEARCH.md) for the dependency decision and verified source capabilities.

Stack: Python stdlib HTTP/SQLite + vanilla HTML/CSS/JS; gallery-dl adapter; Requests/PySocks; yt-dlp; imageio-ffmpeg's bundled FFmpeg. All Python dependencies, including transitive ones, are pinned. No Node build step is needed. Engine-specific internals are confined to `instagram_local/engine.py`. Updating the engine requires changing its pin, reviewing parser/cursor behavior, running tests, and repeating the small authenticated smoke test. Do not assume a dependency update is compatible just because it installs.

Third-party packages retain their own licenses (gallery-dl GPL-2.0-only; yt-dlp Unlicense; Requests Apache-2.0; FFmpeg's bundled build has its own license terms). This is a personal source checkout, not a standalone redistributed executable.
