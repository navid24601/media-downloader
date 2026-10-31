# Original-audio and filename fix — 2026-10-28

**77 tests pass** under normal Windows user access, including all existing Instagram/Library/launcher checks and eight additional regressions. JavaScript syntax, Python compilation, dependency consistency, and diff whitespace checks pass. No dependency or database schema change was needed.

Live metadata-only checks of `Yctn_C_6cF8` reproduced the wrong-language decisions: bitrate-only selection chose Indonesian Opus `251-8` for MP3 and French AAC `140-0` for MP4. Both corrected selectors choose English (US), identified as original: `251-9` for MP3 and `140-9` paired with 360p video `134` for MP4. No full download of the reported video was performed.

Local fixtures verify original-language priority over bitrate, non-English originals, refusal of ambiguous/unusable originals, combined dubbed formats not overriding separate original audio, DRC preference, source requests for only the original track during real yt-dlp/FFmpeg conversion/merging, and persisted audio metadata. Staging checks prevent old dubbed media from being reused, retain correct partials, and refuse legacy finalization receipts without modifying existing completed files. Filename checks cover title/channel snake_case, Windows sanitization, bounded lengths, local date, MP3/MP4 extensions, matching sidecars, collisions, Library indexing, and interrupted finalization recovery.

The example base is `most_epic_deaths_in_history-serious_history-Yctn_C_6cF8-2026-10-28`. Existing completed outputs and duplicate history are preserved. Restart the app, enable **Download again**, and queue the video in each desired output mode to replace an older dubbed choice with a separate corrected copy.

## Previous YouTube update verification — 2026-10-28

The updated suite passes **69 tests** under normal Windows user access: all 48 existing Instagram/Library/launcher checks and 21 YouTube checks. Python compilation, both JavaScript syntax checks, and `pip check` pass. Dependencies now pin yt-dlp 2026.10.26 and its matching yt-dlp-ejs 0.8.0; Node 24.11.1 was present and supported. The existing Instagram DASH video-plus-audio merge fixture passes with the updated yt-dlp release.

New local checks cover:

- Canonical YouTube videos, Shorts, completed-live URLs and playlist routing; mixed TXT/CSV/pasted inputs; explicit ambiguous video/playlist selection; unsupported channels and hostile URL forms; per-line errors and deduplication.
- Quality ceilings, lower-resolution fallback, compatible MP4 codec/audio selection, explicit failure when no suitable format exists, and cached higher-resolution files not satisfying a lower ceiling.
- The actual yt-dlp download/merge pipeline with in-memory media responses, plus real local FFmpeg MP3 conversion and MP4/MP3 segment output. Container headers, decoded streams, actual quality metadata, and shared proxy configuration are checked. No network downloader or shell is used for local conversion.
- Millisecond segment identities, MP3/MP4 separation, active/completed duplicates, missing-file re-download, a simulated interruption after final file rename and before SQLite success, and failures never indexing partial outputs.
- Flat lazy playlist iteration bounded to 50 entries, restart offsets, ordered atomic membership checkpoints even for skipped videos, multiple memberships, iterator reuse between batches, and Library pagination in recorded playlist order.
- Additive/repeatable schema migration, original settings/records, persistent Favorites/hidden state, local metadata allowlisting, platform filters, title/channel/date/link/ID/filename search, and bounded 48-item pages over 5,000 YouTube records.
- Separate YouTube cookie import/clear without touching Instagram, in-memory engine cookies, TLS/proxy settings, no cookie files/info JSON/disk cache, fixed sensitive-data-free errors, limited transient retries, and immediate restriction pauses. The existing DPAPI and media-route authorization/path/range tests remain passing.
- MP3 range streaming with `audio/mpeg`, local waveform/artwork previews, invalidation after artwork changes, and exact recorded subtitle/media deletion.

In-app Chromium checks used `tests/media_ui_fixture.py`: generated Instagram image/video/carousel outputs plus YouTube MP4/MP3 and a playlist, with outbound Requests and yt-dlp requests disabled and no running download worker. Downloader opened first. Switching tabs retained an unsaved URL. Both YouTube files reached readyState 4 from authenticated localhost media URLs; MP3 playback advanced with no media error. Platform/playlist filtering worked, metadata and standard controls were present, and no JavaScript errors were reported.

A small public smoke test succeeded using yt-dlp 2026.10.26 and **no account cookies**: `jNQXAC9IVRw` downloaded as a **240p MP4** under a 360p ceiling and passed a complete local FFmpeg decode. It passed again after restricting selection to native HTTPS/DASH; the final output was **629,172 bytes**. Both unique temporary directories were cleaned automatically. An earlier attempt at the engine's historical public test video returned unavailable. There were no large, whole-playlist, high-volume, or account stress tests.

Still unverified live: account-only/private YouTube videos and playlists, playlist pagination against YouTube, optional subtitles/artwork retrieval, restrictions/Retry-After from a real server, and user-specific VPN/proxy configurations. Those paths have fixture coverage. MP4 segments intentionally use nearby keyframe boundaries; HLS-only/DRM/unsupported-codec formats are intentionally refused. No new authenticated Instagram smoke test was attempted.

## Previous version 2 verification — 2026-10-27

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
