# Engine decision

Research date: 2026-10-25. Browser checks used primary documentation and maintained project source. Implementation was checked against the **installed gallery-dl 1.32.17 wheel**, because the GitHub master view returned by search differed from the installed release. The adapter and fixture contract are pinned to that wheel.

Sources:

- [gallery-dl 1.32.17 release](https://github.com/mikf/gallery-dl/releases/tag/v1.32.17), [PyPI release metadata](https://pypi.org/project/gallery-dl/1.32.17/).
- [Maintained Instagram extractor](https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/instagram.py), [tagged release source](https://github.com/mikf/gallery-dl/blob/v1.32.17/gallery_dl/extractor/instagram.py).
- [gallery-dl configuration](https://gdl-org.github.io/docs/configuration.html), specifically Instagram cursors, media ordering, video/DASH, cookies, and proxies.
- [Instaloader documented targets/authentication](https://instaloader.github.io/cli-options.html) and [implementation](https://github.com/instaloader/instaloader/blob/master/instaloader/structures.py).
- [Requests proxy behavior](https://requests.readthedocs.io/en/latest/user/advanced/#proxies), supplemented by inspection of installed Python 3.13's Windows `urllib.request.getproxies()` and gallery-dl's actual Requests session construction.
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) provide maintained DASH handling and a locally bundled FFmpeg executable.

## Capability checks

| Capability | Evidence and implementation decision |
| --- | --- |
| Public/authenticated single posts and Reels | `InstagramPostExtractor`, session cookie check, and media retrieval. Anonymous access is attempted; Instagram may require a session even for public posts. |
| Images/videos/mixed carousels | The installed parser exposes each carousel item and video variants. Adapter checks raw count against parsed media and refuses missing video URLs instead of saving thumbnails. |
| Best usable quality | Largest exposed image candidate; engine-provided DASH passed to yt-dlp's parser; best video/audio merged without encoding. Missing DASH stays an explicit failure, with no hidden quality downgrade. |
| Profile enumeration | Installed timeline and Reels methods are paginated generators. Both streams are scanned and deduplicated; Latest N uses publication data, including pinned candidates. |
| Saved and named collections | Dedicated maintained Saved/collection extractors exist. Cookie-authenticated REST pagination is used; no invented endpoint is implemented here. Collection selection requires its Instagram name/ID URL. No maintained collection-list discovery method was found. |
| Saved order | No reliable recently-saved contract or save timestamp verified. Source order is preserved and explicit Latest N fallback acceptance is required. |
| Pagination/recovery | Engine cursor configuration and `_cursor` updates plus a persisted within-page offset; queue entries/checkpoint commit together. Engine internals are contained in one adapter. |
| Session persistence | Import browser-exported cookies, protect with current-user Windows DPAPI, inject into engine memory. No password login, browser-cookie decryption assumption, or engine disk cache. |
| Proxy | Resolve static system/environment proxy with Python Windows urllib; pass explicit mapping to Requests/yt-dlp. Explicit HTTP/SOCKS option is available; PAC is not executed. TLS verification enabled. |
| Failure reporting | Application classifies HTTP status, redirects, and known machine-readable error markers before engine fallback catches can swallow restrictions. Raw server errors and signed URLs are never exposed. |

Instaloader documents Saved posts and public/private profile downloads, but the inspected source/docs did not establish named Saved collections. gallery-dl has explicit collection support and a small enough adapter surface. Maintaining a new Instagram API implementation was therefore unnecessary.

The research verifies **source support**, not live operational availability. Instagram can change private endpoints or impose account-specific restrictions. Authenticated Saved, collection, private-profile, and six-link live smoke checks remain pending the user's session and selected sample links. The fallback for a blocked collection is its individual canonical post/Reel links, clearly identified as a separate workflow.
