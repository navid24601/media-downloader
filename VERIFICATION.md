# Version 2 verification — 2026-10-27

The full suite passes **48 tests** under normal Windows user access: the 29 existing tests and 19 new Library tests. Python compilation, both JavaScript syntax checks, and `pip check` pass. No dependencies were added; the existing bundled FFmpeg provides local thumbnails.

Verified with local fixtures:

- A database with only version 1 tables migrates image, video, and mixed-carousel records. Output bytes, settings, pending queue, download history, and a session-file sentinel stay intact. Repeating the migration or restarting after a small migration batch produces no duplicate entries or network downloads.
- Future completion records captions independently of caption-file settings, media types/count, canonical identity/link, and known source labels. Signed media URLs are absent from persisted data.
- Numbered ZIP entries are read in media order even when central-directory order differs. Both uncompressed and deflated members are read without persistent extracted folders.
- Date/link/shortcode/filename search, account filtering, local Favorites, hidden/restored entries, and restart persistence behave as specified.
- Missing, corrupt, staging, traversal, duplicate-numbered, and simulated reparse files are handled visibly or refused. Preview failures do not prevent access to playable originals. Cache generation, signature invalidation, and pruning are tested.
- Deletion requires a fresh confirmation plan naming only the recorded media and recorded sidecar. Changed files and unrelated sidecars are refused. Download history survives, and missing-file duplicate handling permits another download.
- Media routes reject missing authorization, cross-origin browser requests, incorrect Host values, and path-based/traversal requests. Loose and ZIP-contained videos return correct open-ended, suffix, and bounded byte ranges, HEAD responses, and 416 responses for invalid ranges.
- A 5,000-record fixture stays within 48 entries per response, uses disjoint cursor pages, and searches without decoding media or loading all original files.
- Explicit missing-details jobs use the existing worker, update only missing metadata, do not download media or rewrite completed-download records, and pause on account restrictions without repeated attempts.

The Windows DPAPI round trip, encrypted storage checks, and the complete occupied-port / duplicate-launch / clean-shutdown integration test all pass. This resolves the launcher rerun that was pending in the version 1 verification record.

Browser checks in the Codex in-app Chromium browser used real generated JPEG/PNG/MP4 and a mixed ZIP, with outbound Python requests disabled and the download worker not running. They verified Downloader as the opening tab, local Library tiles, image decoding, a carousel's video reaching readyState 4 and playing with standard controls, arrow-key / slider navigation, Favorites, hiding/restoring, date search, and preservation of an unsaved Downloader link across tab switches. A restart retained Favorites. All three thumbnails loaded as 320-pixel cached local images and the browser reported no script errors. The explicit online-details confirmation queued a task locally without starting it; the deletion confirmation listed the exact fixture file and was canceled. Internet disconnection of the user's Windows connection was not required or performed.

Live Instagram remains outside this fixture verification. No new account session or sample links were supplied. Public/private retrieval, account-specific profile/Saved/collection pagination, and the optional online details fetch still need an authenticated small smoke test to establish current Instagram availability. Version 1's normal offline fixture checks for those workflows remain passing; no live successful downloads or account stress tests are claimed.
