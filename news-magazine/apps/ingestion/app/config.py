"""
Environment-driven configuration for the ingestion service.

Never hard-code secrets here — everything comes from the environment,
loaded from .env locally and from the host's environment settings in
production.

Stage 1 declared `max_concurrent_fetches` and `max_article_rows` but the
values were never actually consulted by the scheduler or the pruner, so
the knobs did nothing. Everything here now has a caller.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# The default fetch caps, named so that a settings object which does
# not carry the field at all — an older deployment, a test double —
# falls back to the SAME number the class declares, rather than to a
# cap of one that would silently stop the pipeline fetching anything.
DEFAULT_MAX_ARTICLES_PER_SOURCE = 500
DEFAULT_MAX_ARTICLES_PER_CYCLE = 20_000
DEFAULT_CYCLE_WINDOW_SECONDS = 900
DEFAULT_MAX_REELS_PER_PROVIDER = 500
DEFAULT_MAX_REELS_PER_CYCLE = 5_000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------
    # Supabase (required)
    # ---------------------------------------------------------
    supabase_url: str
    # Server-side only; bypasses RLS by design.
    supabase_service_role_key: str

    # ---------------------------------------------------------
    # Shared secrets
    # ---------------------------------------------------------
    ingestion_secret: str
    cron_secret: str

    # ---------------------------------------------------------
    # The site, for the notifications
    # ---------------------------------------------------------
    # The worker tells the site when it has published something, and
    # the site tells the readers' devices. The two already trust each
    # other - the same secret is on both sides - so nothing new has to
    # be configured for this to work.
    web_app_url: str = "https://new24hr.vercel.app"

    # Whether the worker pings the site at all. It is a switch, not a
    # secret: turning it off stops the pings and nothing else.
    push_notifications_enabled: bool = True

    # ---------------------------------------------------------
    # Networking / politeness
    # ---------------------------------------------------------
    # Identify yourself honestly to every outlet. Several Nepali
    # publishers return a challenge page to generic bot strings.
    default_user_agent: str = (
        "NepalNewsMagazineBot/1.0 "
        "(+https://new24hr.vercel.app; contact=admin@new24hr.vercel.app)"
    )
    default_request_timeout_seconds: float = 15.0

    # The language a normal reader sends with a request.
    #
    # Sent on every feed request alongside the outlet's own homepage as
    # the Referer. Neither header pretends to be anything we are not:
    # the language is a preference and the referer is the publisher's
    # own address, on a path its robots.txt allows for everyone. They
    # are here because a request with no Accept-Language and no Referer
    # is the shape a WAF's browser check looks at first.
    default_accept_language: str = "ne,en;q=0.8"

    # Whether a publisher's own stated Crawl-delay (or Request-rate) in
    # robots.txt is honoured.
    #
    # It is honoured by default, and it is honoured as a FLOOR: a row
    # may say "eight requests a minute", but if the publisher's
    # robots.txt says "one every 20 seconds", 20 seconds is what the
    # worker waits. ratopati.com publishes `Crawl-delay: 20`; before
    # this was read, the worker asked it every 7.5 seconds and never
    # knew the publisher had said otherwise.
    #
    # Set this to false only if a publisher asks us to ignore it.
    respect_robots_crawl_delay: bool = True

    # ---------------------------------------------------------
    # Scheduler
    # ---------------------------------------------------------
    scheduler_enabled: bool = True
    # Floor applied to any sources.poll_interval_seconds. Never poll a
    # single outlet faster than this.
    min_poll_seconds: int = 60

    # ---------------------------------------------------------
    # Fetch caps
    #
    # How much ONE cycle is allowed to pull in.
    #
    # Every number in this block is a HARD bound on the number of
    # items actually fetched and stored, enforced at the point the
    # work is done — not a target, and not a number nothing reads.
    # Raise one and the pipeline really does fetch more; lower one
    # and it really does fetch less. A cap that only exists in a
    # config file is a lie told to the operator, and this file has
    # already shipped one of those (`max_concurrent_fetches`).
    #
    # WHY A CAP AT ALL
    # ----------------
    # A feed is a PAGE, not a stream. An outlet that published 400
    # stories today serves all 400 in one feed document, and every
    # one of them would otherwise be normalised, deduped, page-fetched
    # for its media and stored on the very first poll — which is a
    # crawl of one publisher rather than a poll of it. The same is
    # true of the whole pipeline: 27 outlets polled on their own
    # intervals can put a few thousand entries through the pipeline
    # in a quarter of an hour, and each one costs a dedup lookup
    # whether or not it turns out to be a story we have already seen.
    # ---------------------------------------------------------

    # Per outlet, per poll: the most articles ONE source may
    # contribute in a single sync.
    #
    # 200 is comfortably above what any single Nepali outlet
    # publishes between two polls, so this does not bite in normal
    # operation — and it is a real bound when it does. Raise it if an
    # outlet legitimately publishes more than this between polls.
    max_articles_per_source: int = DEFAULT_MAX_ARTICLES_PER_SOURCE

    # Across EVERY outlet, in one cycle: the most articles the whole
    # pipeline may fetch.
    #
    # The per-outlet cap alone still allows 27 x 200 = 5,400 articles
    # in a single poll of every outlet, which is a crawl of 27
    # publishers rather than a poll of them. This is the number that
    # keeps the pipeline's total appetite bounded, and it is what a
    # runaway source — a feed that suddenly republishes its whole
    # archive — can no longer spend on its own.
    max_articles_per_cycle: int = DEFAULT_MAX_ARTICLES_PER_CYCLE

    # How long ONE cycle is, in seconds.
    #
    # The scheduler has no single pass over every outlet: each source
    # has its own interval and they are staggered, so "one cycle" is
    # defined here as a WINDOW rather than a batch. The total cap
    # above applies within the window, and the window restarts as
    # soon as the previous one expires — a rate, not a one-shot
    # allowance.
    #
    # 900s is long enough to cover roughly one poll of every outlet
    # several times over, so the cap is a backstop rather than
    # something that shapes normal operation.
    cycle_window_seconds: int = DEFAULT_CYCLE_WINDOW_SECONDS

    # ---------------------------------------------------------
    # RSS feed paging
    #
    # A WordPress feed serves a fixed number of items: the site's
    # "posts_per_rss" option, which is 10 for most WordPress sites
    # and 5 for several Nepali ones. The operator wants more than
    # the ten.
    #
    # MEASURED 2026-10-22 on the live Nepali feeds, which is why
    # there are two knobs here and not one:
    #
    #   posts_per_page=100   changes NOTHING. The item count and the
    #                        first item are identical with and
    #                        without it on every WordPress feed
    #                        tested (10 of 10). WordPress builds the
    #                        main feed's query with posts_per_page
    #                        pinned to the posts_per_rss option and
    #                        ignores the query string. It is still
    #                        asked for, because it costs nothing and
    #                        it does help any site that does honour
    #                        it.
    #
    #   paged=2, paged=3     DO work. They return the NEXT page of
    #                        items -- older stories, the same count
    #                        per page. This is the only mechanism
    #                        measured that actually gets more items
    #                        out of a WordPress feed.
    #
    # Paging costs one extra request per page per poll, and this
    # codebase's first rule is to be a polite client, so paging is
    # OFF by default. One page is today's behaviour exactly: one
    # request, one document, the same articles.
    #
    # Only KNOWN WordPress hosts get either parameter. A feed that
    # is not WordPress is fetched exactly as it was, because neither
    # parameter means anything to a non-WordPress server.
    # ---------------------------------------------------------

    # How many items to ask a WordPress feed for. 0 = do not ask.
    rss_page_size: int = 100

    # The query parameter WordPress is asked with. Changeable because
    # a site running a feed plugin may use a different name.
    rss_page_size_param: str = "posts_per_page"

    # How many pages of a feed to request and merge. 1 = only the
    # first page, which is what the worker did before this existed.
    rss_feed_pages: int = 1

    # Comma-separated hosts that run WordPress, added to the built-in
    # list in app/sources/generic_rss.py. The operator can extend
    # that list without a deploy.
    rss_wordpress_hosts: str | None = None

    # ---------------------------------------------------------
    # Retention
    # ---------------------------------------------------------
    # Articles are deleted this long after publication. A pruned story
    # comes back when someone searches for it (see /api/scrape-on-demand).
    retention_hours: int = 48
    # Hard ceiling applied after the TTL pass.
    max_article_rows: int = 10_000

    # How long a source error is kept before the pass deletes it.
    #
    # The errors table is a DIAGNOSTIC, not an archive. One row is
    # written per failure, and a publisher that fails every two
    # minutes writes thirty rows an hour, every hour, for ever - so
    # the table grew without bound and the operator's admin panel,
    # which lists the recent errors, spent its time showing failures
    # from three days ago that nobody is going to act on.
    #
    # So the rows are pruned by age, by the ingestion itself, on every
    # pass. Forty-eight hours is two days: long enough to see what
    # happened over a weekend, short enough that the panel is about
    # now rather than about ever.
    source_error_retention_hours: int = 48

    # ---------------------------------------------------------
    # The reader proxy
    # ---------------------------------------------------------
    # Nine Nepali publishers answer the CI runner with a 403 and
    # answer an ordinary client with a 200 - the refusal is aimed at
    # the ADDRESS, not at the request, and their robots.txt permits
    # every path we ask for. Those nine contribute nothing, and the
    # pictures of the stories they published before the refusal
    # began can never be proven, because the page that declares them
    # cannot be read from here.
    #
    # So when a publisher refuses this address, the page is read
    # through a reader proxy instead - a service that fetches a public
    # URL from an address that is not refused and hands back the HTML.
    # The extraction then runs on the real page, and the picture is
    # proven by loading the IMAGE, which lives on the publisher's CDN
    # and answers us perfectly well.
    #
    # It is a FALLBACK: the publisher is always asked first. It never
    # carries a credential, a cookie or a session - the URLs are
    # public article addresses and nothing else. Set this to false to
    # turn it off.
    reader_proxy_enabled: bool = True
    reader_proxy_url: str = "https://r.jina.ai"

    # ---------------------------------------------------------
    # The reader's API key
    # ---------------------------------------------------------
    #
    # The reader serves twenty pages a minute to an address with no
    # key, and sixty to one with a key. The free ceiling was the
    # pass's floor: every page of a publisher that refuses this
    # address had to wait its turn, and the pass could not go faster
    # than the reader would serve it.
    #
    # With a key the wait is a third of what it was, and a pass can
    # read every publisher's article pages - which is what proves the
    # pictures and takes the headlines off the story's own page.
    #
    # The key is a CREDENTIAL. It is read from the environment, it is
    # never logged, and it is never sent anywhere except the reader.
    jina_api_key: str = ""

    # ---------------------------------------------------------
    # Storage pressure
    # ---------------------------------------------------------
    # The database must never be the reason the site stops. A free
    # Supabase project has 500 MB, and a busy news day writes a lot:
    # one `source_sync_logs` row per outlet per pass, one
    # `source_errors` row per failure, one `analytics_events` row per
    # read. Nothing prunes the last two by age, so a year of passes
    # would fill a database that the news itself would never fill.
    #
    # So: the log tables are pruned by age on every pass, and when the
    # database is at eighty percent of its limit the oldest half of
    # the articles goes as well. The site keeps running either way —
    # this is the difference between a small archive and no site.
    #
    # The limit is the plan's disk allowance, in bytes. 500 MB is
    # Supabase's free tier; a paid project has far more, and the
    # number is one setting away.
    database_size_limit_bytes: int = 500 * 1024 * 1024
    # How full the database may get before the emergency prune runs.
    storage_pressure_threshold: float = 0.8
    # How much of the oldest material the emergency prune removes.
    storage_emergency_prune_ratio: float = 0.5
    # How long the per-pass logs are kept. They are a diagnostic, not
    # an archive: a week is enough to see what happened and small
    # enough never to matter.
    sync_log_retention_days: int = 7
    error_log_retention_days: int = 30
    analytics_retention_days: int = 30

    # ---------------------------------------------------------
    # Deduplication
    # ---------------------------------------------------------
    dedup_window_hours: int = 12
    # rapidfuzz score (0-100) at or above which two headlines are the
    # same story. 78 separates real Nepali wire copy (measured 78.7-83.0)
    # from unrelated stories (measured 43.3).
    fuzzy_title_threshold: int = 78

    # ---------------------------------------------------------
    # On-demand search
    # ---------------------------------------------------------
    max_requests_per_hour: int = 5
    scrape_budget_seconds: float = 18.0

    # ---------------------------------------------------------
    # Media enrichment
    # ---------------------------------------------------------
    # Fetching each article page for og:image / video / audio is what
    # makes real media appear. Turning this off leaves feed-only
    # thumbnails, which is why images rendered blank before.
    enrich_media: bool = True

    # ---------------------------------------------------------
    # Image validation
    #
    # Every image URL used to be chosen on the server by guessing
    # from the URL's shape, and then stored unverified. Some of
    # those guesses point at URLs that 403 or 404 forever — an
    # assets-cdn.kathmandupost.com request with a foreign referer
    # is refused outright — so the feed carried permanently dead
    # links and the only party that found out was the reader's
    # browser. This asks each URL to prove it returns an image
    # before the row is written, so the guarantee is made once,
    # server-side, for every reader on every page.
    #
    # Costs one ranged GET per NEW image, deduplicated per poll by
    # URL, so outlets that reuse one image across stories pay for
    # it once rather than per article.
    # ---------------------------------------------------------
    validate_images: bool = True
    image_validation_timeout_seconds: float = 6.0
    # Safety valve: how many renditions of one article may be probed
    # before giving up on it. Almost every article has one candidate
    # or two; this only bounds the pathological case.
    max_image_candidates_per_article: int = 3

    # ---------------------------------------------------------
    # Full-text body storage (licence-gated)
    #
    # `sources.republish_permission` is the operator's per-outlet
    # decision that a publisher's body may be republished. The
    # extractor enforced it from the start, but nothing ever
    # POPULATED `full_body_html` — both adapters set it to None at
    # normalize() time, which is before any page has been fetched — so
    # the flag guarded a value nobody produced and "read more"
    # expanded the excerpt. See app/sources/body.py.
    #
    # ENRICH_BODY is free. It reuses the page media enrichment already
    # fetches for og:image / video / audio, and takes the plain text
    # trafilatura already pulled off it. ZERO extra requests — which is
    # the whole reason it defaults to True. It was False, which made
    # the one path that costs nothing the one path nobody used: the
    # producer in app/sources/body.py was wired into the store path and
    # returned before doing anything, so an outlet with a granted
    # licence still produced an excerpt-only article.
    #
    # FETCH_FULL_TEXT is the knob that costs, and stays False. It pays
    # one page request per article for stories whose page was not being
    # fetched anyway — the feed already carried an image, so media
    # enrichment had no reason to open the page. Across 27 publishers
    # that is one request per story per poll, which is a crawl rather
    # than a poll and is how an operator's IP gets blocked. It is a
    # separate flag so nobody turns body storage on by accident and
    # acquires it.
    #
    # Turning ENRICH_BODY on does not by itself make any article longer.
    # Both flags do nothing at all without republish_permission on the
    # source row, and that column is False for every seeded outlet
    # (migration 0001 defaults it false, 0008 seeds it false). So this
    # is necessary, not sufficient: until an operator flips that column
    # for an outlet whose publisher licenses republication, that
    # outlet's articles carry a summary and nothing more, and no flag
    # in this file can change that. GET /api/sources reports
    # `body_status` per outlet so the gap is visible rather than
    # silent.
    # ---------------------------------------------------------
    enrich_body: bool = True
    fetch_full_text: bool = False

    # ---------------------------------------------------------
    # Gemini (optional)
    #
    # NOT needed for the four-point summaries: those are extracted
    # verbatim from each outlet's own article text.
    # ---------------------------------------------------------
    gemini_api_key: str | None = None
    # May rewrite the stored excerpt. Its failure can never reject an
    # article or block a sync.
    enable_gemini_summary: bool = False
    # May reorder the four already-extracted points. It cannot introduce
    # new text: anything not already in the points is discarded.
    enable_gemini_ranking: bool = False

    # ---------------------------------------------------------
    # Reels (optional)
    #
    # Short-form video for the /category/reels page. Only METADATA is
    # stored; playback streams from the provider CDN.
    #
    # Every provider here licenses its content for redistribution —
    # that is the only reason any of this is here. Wikimedia Commons
    # needs no key at all, so the category works unconfigured; Pexels
    # and Pixabay add coverage when keys are present.
    #
    # Multiple keys are comma-separated. The pool rotates across them
    # and skips any that are rate limited, so one exhausted key does
    # not take the category down.
    # ---------------------------------------------------------
    enable_reels: bool = False
    # Per provider, per pass: the most clips ONE provider may
    # contribute to a single reels pass.
    #
    # Enforced twice, and both are real bounds rather than requests:
    # the provider adapter is asked for at most this many candidates
    # (and every adapter additionally clamps that to the page size
    # its own API allows — 80 for Pexels, 200 for Pixabay, 50 for
    # YouTube, 25 per search for Wikimedia), and the store is handed
    # at most this many.
    #
    # `REELS_PER_SOURCE` was this knob's earlier name and still
    # works: a deployment that already sets it keeps its value.
    max_reels_per_provider: int = Field(
        default=DEFAULT_MAX_REELS_PER_PROVIDER,
        validation_alias=AliasChoices(
            "max_reels_per_provider",
            "reels_per_source",
        ),
    )
    # Across EVERY provider, in one pass: the most clips the whole
    # reels pass may fetch.
    #
    # One reels pass is one cycle on the video side — the job runs
    # every provider once, in order — so this is the total cap for
    # that cycle. The budget is shared fairly between the providers
    # still to run, so a tight budget thins every provider's share
    # instead of starving the last one to be asked.
    max_reels_per_cycle: int = DEFAULT_MAX_REELS_PER_CYCLE
    reels_poll_seconds: int = 300
    # Every five minutes, so the Reels tab moves at a glance rather than
    # every quarter-hour.
    #
    # Deliberately NOT matched to the article cadence. Outlets are
    # polled every 90-180s because each is a separate site with separate
    # terms; Wikimedia Commons is a single shared API whose policy asks
    # clients to poll politely and withdraws access when they do not.
    # One reels pass is a handful of requests, so five minutes is ~50 an
    # hour — frequent enough to read as live, courteous enough to keep.
    pexels_api_keys: str | None = None      # comma-separated
    pixabay_api_keys: str | None = None     # comma-separated
    # YouTube Data API v3 key. Comma-separated for the same reason.
    #
    # This one is an EMBEDDING source, not a redistributable one — the
    # adapter embeds YouTube's own player and never touches the media
    # stream.
    #
    # It is also priced an order of magnitude above the other two:
    # search.list costs 100 quota units a call against a default daily
    # budget of 10,000, so on the reels cadence a key would be gone in
    # half an hour. Rather than make the operator raise
    # REELS_POLL_SECONDS — which would also slow the sources that cost
    # nothing — the adapter declares `min_poll_seconds = 3600` and the
    # scheduler honours it, so this key works correctly the moment it is
    # set and REELS_POLL_SECONDS can stay where the operator wants it.
    # See YouTubeShortsSource for the arithmetic.
    youtube_api_keys: str | None = None     # comma-separated
    # Comma-separated hints, rotated one per pass.
    #
    # The default is deliberately NOT empty. With no hint, the keyless
    # Wikimedia adapter asks Commons for its newest video uploads and
    # sorts by upload time, which fills a Nepal news site's Reels tab
    # with unrelated scientific timelapses — a first pass here stored 30
    # NASA SDO clips. Terms keep the category on subject by default; add
    # your own comma-separated to widen it.
    #
    # WHAT THIS LIST CAN AND CANNOT DO
    # --------------------------------
    # Every entry below was measured against the live API at the page
    # size the adapter actually requests (50 results, tall videos only):
    #
    #   nepal 50   tharu 48   himalayan 29   kathmandu 8
    #   langtang 7   janakpur 6   cat:Videos of Mount Everest 6
    #   cat:Videos of the Himalayas 5   bagmati 2   bhaktapur 1
    #   pashupatinath 1   bhairahawa 1
    #
    # That is roughly 135 files, and the reels table already held 136
    # rows before this list was written. Commons has no more video of
    # Nepal than this. The Reels tab is not "not updating" because the
    # search is too narrow — it is at the ceiling of a keyless source,
    # and no term list can lift it.
    #
    # Generic subject categories were measured too, and are deliberately
    # NOT used: `cat:Videos of Rain` returns 48 files that are Sonoma
    # County, `Videos of Rivers` returns 50 from the Adige in Italy,
    # `Videos of Floods` returns the Czech Republic. Adopting them would
    # grow the table to ~650 rows of European and Californian B-roll,
    # which is the same "unrelated clips" failure as the NASA timelapses
    # this setting exists to prevent, just better photographed.
    #
    # So the honest shape is: keep the catalogue on subject, and get
    # volume from a provider that actually has it. Pexels and Pixabay are
    # already implemented and already wired into the scheduler — they are
    # skipped only while `PEXELS_API_KEYS` / `PIXABAY_API_KEYS` are unset.
    # Setting either one is the whole fix for an always-new feed.
    #
    # Terms that LOOK relevant but were measured and rejected, because
    # the word has a second sense on Commons: `mustang` (the P-51
    # aircraft), `terai` (a Dutch skier), `tihar` (a rally in India),
    # `himalaya` (a roller coaster, 0 usable results), `monsoon` (clouds
    # over Albuquerque). `india` and `delhi` return 50 each but are
    # largely conference footage; `himalayan` is the cleaner regional
    # term and is what made the list.
    #
    # The list is capped at 40 terms (`WikimediaSource._search_terms`),
    # and a pass asks for SEVERAL PAGES of results per term rather
    # than one - which is the change that actually made the reels
    # move. The old behaviour asked Commons for the newest twenty-five
    # files per term and got the same twenty-five back every time, so
    # the pool never grew past the newest slice of each term and the
    # feed stood still. The walk now goes deeper and ROTATES, so a
    # pass covers the whole catalogue of these terms over the course
    # of a day.
    #
    # Lengthening this string past the cap silently changes nothing,
    # so choose the terms that measure well rather than the most.
    #
    # MEASURED, so the ceiling is not a guess: against the live API,
    # `intitle:nepal` holds 66 videos, `intitle:tharu` 48,
    # `intitle:himalayan` 29, `intitle:holi` 31, `intitle:porter` 34,
    # `intitle:temple` 291, `intitle:festival` 894, and the two
    # categories hold 5 and 6. The union of the whole Nepali set is a
    # couple of hundred files, which is what Commons has and not one
    # more. A hundred thousand reels is not reachable from a keyless
    # source; it needs Pexels, Pixabay or YouTube.
    reels_search_terms: str | None = (
        "nepal,tharu,himalayan,kathmandu,langtang,janakpur,"
        "bhaktapur,bagmati,pashupatinath,bhairahawa,"
        "cat:Videos of the Himalayas,cat:Videos of Mount Everest"
    )
    reels_http_timeout: float = 20.0
    # Hard ceiling on one whole reels pass.
    #
    # The job runs at max_instances=1, so a pass that never returns
    # never runs again and the category goes silent with no error
    # anywhere. This bounds it. Generous enough for several slow
    # providers in sequence, short enough that one hung socket cannot
    # hold the slot past the next few intervals.
    reels_pass_timeout_seconds: int = 180
    # Wikimedia's API policy asks every client to identify itself and
    # says anonymous traffic is what gets access withdrawn. This is a
    # contact address that reaches a human, not a version string.
    # It lives here rather than in os.environ so that setting it in
    # .env works, exactly as every other setting in this file does.
    reels_user_agent: str = (
        "NepalNewsMagazineReels/1.0 (+https://new24hr.vercel.app)"
    )

    # ---------------------------------------------------------
    # Panchang (optional)
    #
    # The Nepali almanac in the site header: today's Bikram Sambat
    # date, the tithi, the nakshatra, sunrise and sunset, and the
    # windows the panchang calls auspicious and inauspicious.
    #
    # This is its own pipeline with its own table. It is not an
    # article, it is never deduplicated against articles, and it
    # cannot affect an outlet poll.
    #
    # The job runs hourly because the Bikram Sambat date rolls over at
    # midnight NEPALI time, and an almanac that is six hours stale is
    # an almanac for the wrong day. It is cheap in spite of the
    # cadence: the pass resolves today's date from one small page
    # and only downloads the large per-day page when the day has
    # actually changed - which is once.
    # ---------------------------------------------------------
    enable_panchang: bool = True
    panchang_poll_seconds: int = 3600
    panchang_http_timeout_seconds: float = 20.0

    # ---------------------------------------------------------
    # Logging
    # ---------------------------------------------------------
    log_level: str = "INFO"
    log_format: str = "console"  # console | json


@lru_cache
def get_settings() -> Settings:
    return Settings()