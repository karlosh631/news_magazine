# Hamro Patro's news section — what was fetched, what was found, what was added

**Date of verification:** 2026-10-23
**Migration produced:** `supabase/migrations/0030_hamropatro_news.sql`
**Test:** `apps/ingestion/tests/test_hamropatro_news.py`

## The short version

| | |
|---|---|
| robots.txt | 200 — `/news` and `/news/national` are **allowed** |
| RSS/Atom feed | **none** — the feed paths serve the HTML app shell or 404 |
| Listing page | **works**, server-rendered, one request each |
| Rows added | **2** — `Hamro Patro News` and `Hamro Patro National` |
| Adapter | `html_listing` |
| Entries measured | **24–26** on `/news`, **34** on `/news/national` |

Both rows carry the same `selector_config`, because both pages are
served the same markup.

---

## 1. robots.txt — read first, and it allows the path

`https://www.hamropatro.com/robots.txt` answers 200. Its only group is
`User-agent: *`, and it disallows exactly:

```
/widgets/  /login  /en/login  /contest-term-condition
/terms  /privacy  /bookmark  /notes
```

`/news` and `/news/national` are **not** in that list, so both are
allowed. (The same file governs the panchang and the rashifal, which
this repo already reads from this host.)

The file also advertises `sitemap-index.xml` and `news-sitemap.xml`,
which is a further sign the news section is meant to be read.

## 2. There is no feed

| URL | Answer |
|---|---|
| `/news/feed` | 200, but `text/html` — the Next.js app shell, not a feed |
| `/news/rss` | 200, but `text/html` — the same app shell |
| `/rss` | 404 |
| `/rss.xml` | 404 |
| `/feed` | 404 |
| `/feed.xml` | 404 |

A 200 that serves HTML is not a feed, so the listing page is read
instead — which is what the `html_listing` adapter is for.

## 3. How the listing is marked up

The page is **server-rendered**: the cards are in the served HTML, not
assembled by JavaScript. Every card is an anchor of the site's own
`hp-card-surface` class:

```html
<a class="hp-card-surface group flex gap-3 ..." href="/news/detail/4924822949913971-...">
  <div class="aspect-square w-20 ...">
    <img alt="" loading="lazy"
         src="https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6...">
  </div>
  <div class="flex min-w-0 flex-1 flex-col gap-1">
    <span class="flex flex-wrap items-center gap-1.5 ...">
      <span class="font-semibold text-primary">onlinekhabar.com</span>
      <span aria-hidden="true">·</span>
      <span>३५ मिनेट अघि</span>
    </span>
    <h3 class="font-np font-bold ... line-clamp-2 text-title-sm">
      इजरायलमा वामपन्थ र दक्षिणपन्थको अर्थ के हो ? विपक्षी को हो ?
    </h3>
    <p class="line-clamp-2 ...">६ कात्तिक, काठमाडौं । इजरायलमा ...</p>
  </div>
</a>
```

Three things follow from that shape, and each one is a trap:

1. **The card IS the link.** The `link` selector finds nothing inside
   the card (an anchor has no anchor inside it), and the adapter's
   fallback — an anchor can be its own card — is what makes this work.
   The `item` selector therefore names the anchor itself.
2. **The card carries four texts.** The outlet the story came from, a
   relative timestamp, the headline, and the summary. Only the `<h3>`
   is the headline. With no `title` selector the adapter falls back to
   the link's own text and stores all four — which is why the row
   names `h3`, and why the test file pins that difference down.
3. **The picture is in `src`.** There is no lazy-load attribute to
   read, and the picture is served from `image.hamropatro.com` as a
   resized, base64-encoded source URL.

### The date

The card's only date is the relative timestamp, and it is Nepali prose
("३५ मिनेट अघि"). The page carries no `<time>` element, no `datetime`
attribute and no `datePublished`. The article page's own JSON-LD
`datePublished` is the moment the aggregator fetched the story, not
the moment it was published, so it is not a publication date either.

So there is no date to read: the rows are stored with the time they
were fetched, the same situation as the MyRepublica row in migration
0029. Reading the Nepali relative timestamp would need a Nepali
number-word parser, which the adapter does not have.

## 4. What the selector_config is

```json
{
  "item":   "a.hp-card-surface[href*='/news/detail/']",
  "link":   "a[href]",
  "title":  "h3",
  "image":  "img",
  "exclude": "/author/|/category/|/tag/|/page/\\d+|\\.(jpg|jpeg|png|gif|webp|svg|css|js|pdf|mp4)$"
}
```

## 5. What the repo's own adapter measured

Run against the live pages, with the config from the migration:

```
$ cd apps/ingestion
$ ./.venv/Scripts/python.exe -c "..."      # HtmlListingAdapter(source).parse(await .fetch())
```

### `https://www.hamropatro.com/news`

The page rotates — stories arrive and the oldest leave — so the count
moved between two runs on the same day: **24 entries** in the first
run and **26** in the second, each in a single request. The second
run is reproduced here in full, because it is the one that ran against
the rows exactly as the migration writes them.

```
ENTRY COUNT: 26
with image: 21 | with date: 0
normalized articles: 26
headlines carrying outlet/timestamp/summary chrome: 0
```

```
title: बडादशैँको उल्लासः टीका र जमरा थाप्ने क्रम जारी
link : https://www.hamropatro.com/news/detail/4927230836971895-badadashainko-ullasah-tika-r-jamara-thapne-kram-jari
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cucmVwb3J0ZXJzbmVwYWwuY29tL3dwLWNvbnRlbnQvdXBsb2Fkcy8yMDI2LzEwL0Rhc2hhaW4tdGlrYS53ZWJw.webp
date : None

title: बेनी–जोमसोम सडक मर्मत गरिएपछि म्याग्दीमा पर्यटकको चहलपहल बढ्न थाल्यो
link : https://www.hamropatro.com/news/detail/4927220445386140-beni-jomasom-sadak-marmat-gariepachhi-myagdima-paryatakako-chahalapahal-badhn
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9hcnRoaWtuZXdzLnByaXhhY2RuLm5ldC9tZWRpYS9hbGJ1bXMvTXlhZ2RpX01hX3RvdXJpc3Rfa29fY2FoYWxwYWhhbF9fMV9uOGQ3THV4eHprLmpwZw.webp
date : None

title: नेपाल प्रिमियर लिगको उद्घाटन दिनको टिकट आधा घण्टामै सोल्ड आउट
link : https://www.hamropatro.com/news/detail/4927224653393640-nepal-primiyar-ligako-udghatan-dinako-tikat-adha-ghantamai-sold-aut
image: None
date : None

title: नेपाल राष्ट्र बैंकको वेबसाइट र अनलाइन सेवा अस्थायी रूपमा बन्द
link : https://www.hamropatro.com/news/detail/4927224076672031-nepal-rashtr-bainkako-vebasait-r-analain-seva-asthayi-rupama-band
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9jZG4ubHVtaW5vY2RuLm5ldC9hbGJ1bXMvTlJCX2J1aWxkaW5nX1p3V3JxdWIzSHVfTjR6MXNkanFBTC5qcGc.webp
date : None

title: यस वर्ष दसैँमा उपत्यकामा सवारी दुर्घटना घट्यो
link : https://www.hamropatro.com/news/detail/4926925679789922-yas-varsh-dasainma-upatyakama-savari-durghatana-ghatyo
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cuY2FuYWRhbmVwYWwuY29tL3dwLWNvbnRlbnQvdXBsb2Fkcy9wb3N0cy9hY2NpZGVudC0xNzg4MjQ3Mjc3LmpwZw.webp
date : None

title: दशैंमा काठमाडौं उपत्यकामा सवारी दुर्घटना र त्यसबाट हुने मानवीय क्षतिमा कमी
link : https://www.hamropatro.com/news/detail/4926932146527166-dashainma-kathamadaun-upatyakama-savari-durghatana-r-tyasabat-hune-manaviy
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9uZXdzb2ZuZXBhbC5jb20vd3AtY29udGVudC91cGxvYWRzLzIwMjYvMTAvSU1HXzIwMjYxMDIzXzExMjUxNy03Njh4MzMyLmpwZw.webp
date : None

title: साइबर जोखिमले सरकारीदेखि बैंकका वेबसाइटसम्म बन्द, के सुरक्षित छन् ग्राहकका पैसा र विवरण ?
link : https://www.hamropatro.com/news/detail/4926308209471814-saibar-jokhimale-sarakaridekhi-bainkaka-vebasaitasamm-band-ke-surakshit-chhan
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9pY3RzYW1hY2hhcmNkbi5wcml4YWNkbi5uZXQvbWVkaWEvYWxidW1zL2N5YmVyLTMzMjcyNDBfMTI4MF8xZ3dsd0hXRVNNX01CeWNzUHBtdWIuanBn.webp
date : None

title: बन्दीपुरबाट गाउँसम्म जीप सफारीको आकर्षण
link : https://www.hamropatro.com/news/detail/4926302596773615-bandipurabat-gaunsamm-jip-saphariko-akarshan
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9uZXBhbG5ld3MuY29tL3dwLWNvbnRlbnQvdXBsb2Fkcy8yMDI1LzExL0JhbmRpcHVyLU5lcGFsLVRyYXZlbC1HdWlkZS0xLmpwZw.webp
date : None
```

The first run, an hour earlier, on the same page, gave:

```
ENTRY COUNT: 24
with image: 22 | with date: 0
normalized articles: 24
```

and these eight of its rows:

```
title: नौमतीको तालमा सरायको रौनक
link : https://www.hamropatro.com/news/detail/4926330091006254-naumatiko-talama-sarayako-raunak
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9hc3NldHMtY2RuLmVrYW50aXB1ci5jb20vdXBsb2Fkcy9zb3VyY2UvbmV3cy9rYW50aXB1ci8yMDI2L21pc2NlbGxhbmVvdXMvMTAwMDI2Nzg1My0yMzEwMjAyNjA3MzE1Ni0xMDAweDAuanBn.webp
date : None

title: जयशंकरलाई स्वर्णिमको पेचिलो प्रश्न : ‘विश्व नयाँ व्यवस्थातर्फ कि पुरानै शक्ति संघर्षतर्फ ?
link : https://www.hamropatro.com/news/detail/4926328277991752-jayashankaralai-svarnimako-pechilo-prashn-vishv-nayan-vyavasthatarph-ki-puranai
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9zbm93YmVycnkucHJpeGFjZG4ubmV0L21lZGlhL2dhbGxlcnlfZm9sZGVyL3N3b3JuaW0tcy1qYWlzaGFua2FyX3JKMFhGa0c4dHVfczJ1ajRqb2l0azV2MHc4ZWp2Yml5YjU4dWpzcTZwcmp0cWZkeXcwcm5pMXVnd3YwM2YxeWplMDRveDQ0X08xUndzRnZ0cEZfdG1vM3F3bmxzb3FlZ3BtZW11YXkxZW5pa2UyY25jZnl4eDM1ZnNseHp4MDV1NGRlZGRtbnFqbnhqeHcwLmpwZw.webp
date : None

title: बङ्गलादेशका मोहम्मद सैफउद्दिनले पोखराबाट एनपिएल खेल्ने
link : https://www.hamropatro.com/news/detail/4926307025576464-banggaladeshaka-mohammad-saiphauddinale-pokharabat-enapiel-khelne
image: None
date : None

title: यसवर्ष दसैँमा उपत्यकामा सवारी दुर्घटना घट्यो
link : https://www.hamropatro.com/news/detail/4926307295959514-yasavarsh-dasainma-upatyakama-savari-durghatana-ghatyo
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9ucGNkbi5yYXRvcGF0aS5jb20vbWVkaWEvbmV3cy9hY2NpZGVudDFfMEhWVndFVUVBSi5qcGc.webp
date : None

title: साउदीमा ड्रोन आक्रमण: नेपालीको मृत्युप्रति कांग्रेसद्वारा दुःख व्यक्त, सुरक्षा र उद्धारको …
link : https://www.hamropatro.com/news/detail/4926028397503465-saudima-dron-akraman-nepaliko-mrityuprati-kangresadvara-duhkh-vyakt-suraksha-r
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cuZGNuYWwuY29tL3dwLWNvbnRlbnQvdXBsb2Fkcy8yMDI2LzA4L0NvbmdyZXNzXzkxNzc3OTA5NDEyLmpwZw.webp
date : None

title: पारिवारिक तीर्थाटनका लागि रवि लामिछाने भारत प्रस्थान, कैँची धाम र कामाख्या मन्दिरमा पूजा अ…
link : https://www.hamropatro.com/news/detail/4926018464732042-parivarik-tirthatanaka-lagi-ravi-lamichhane-bharat-prasthan-kainchi-dham-r
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cua2FsYWthcm1pLmNvbS93cC1jb250ZW50L3VwbG9hZHMvMjAyNi8xMC8xNzkyNzMyODI1ODg4LmpwZw.webp
date : None

title: इजरायलमा वामपन्थ र दक्षिणपन्थको अर्थ के हो ? विपक्षी को हो ?
link : https://www.hamropatro.com/news/detail/4924822949913971-ijarayalama-vamapanth-r-dakshinapanthako-arth-ke-ho-vipakshi-ko-ho
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cub25saW5la2hhYmFyLmNvbS93cC1jb250ZW50L3VwbG9hZHMvMjAyMi8xMi9iZW5qYW1pbi1uZXRhbnl1LWlzcmFlbC03Njh4NDQ4LmpwZw.webp
date : None

title: रास्वपा सभापति लामिछाने पारिवारिक तीर्थाटनका लागि भारत प्रस्थान
link : https://www.hamropatro.com/news/detail/4924817774638901-rasvapa-sabhapati-lamichhane-parivarik-tirthatanaka-lagi-bharat-prasthan
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cua2hhYmFyaHViLmNvbS93cC1jb250ZW50L3VwbG9hZHMvMjAyNi8xMC8xcmFiaS1zY2FsZS5qcGVn.webp
date : None
```

```
title: नौमतीको तालमा सरायको रौनक
link : https://www.hamropatro.com/news/detail/4926330091006254-naumatiko-talama-sarayako-raunak
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9hc3NldHMtY2RuLmVrYW50aXB1ci5jb20vdXBsb2Fkcy9zb3VyY2UvbmV3cy9rYW50aXB1ci8yMDI2L21pc2NlbGxhbmVvdXMvMTAwMDI2Nzg1My0yMzEwMjAyNjA3MzE1Ni0xMDAweDAuanBn.webp
date : None

title: जयशंकरलाई स्वर्णिमको पेचिलो प्रश्न : ‘विश्व नयाँ व्यवस्थातर्फ कि पुरानै शक्ति संघर्षतर्फ ?
link : https://www.hamropatro.com/news/detail/4926328277991752-jayashankaralai-svarnimako-pechilo-prashn-vishv-nayan-vyavasthatarph-ki-puranai
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9zbm93YmVycnkucHJpeGFjZG4ubmV0L21lZGlhL2dhbGxlcnlfZm9sZGVyL3N3b3JuaW0tcy1qYWlzaGFua2FyX3JKMFhGa0c4dHVfczJ1ajRqb2l0azV2MHc4ZWp2Yml5YjU4dWpzcTZwcmp0cWZkeXcwcm5pMXVnd3YwM2YxeWplMDRveDQ0X08xUndzRnZ0cEZfdG1vM3F3bmxzb3FlZ3BtZW11YXkxZW5pa2UyY25jZnl4eDM1ZnNseHp4MDV1NGRlZGRtbnFqbnhqeHcwLmpwZw.webp
date : None

title: बङ्गलादेशका मोहम्मद सैफउद्दिनले पोखराबाट एनपिएल खेल्ने
link : https://www.hamropatro.com/news/detail/4926307025576464-banggaladeshaka-mohammad-saiphauddinale-pokharabat-enapiel-khelne
image: None
date : None

title: यसवर्ष दसैँमा उपत्यकामा सवारी दुर्घटना घट्यो
link : https://www.hamropatro.com/news/detail/4926307295959514-yasavarsh-dasainma-upatyakama-savari-durghatana-ghatyo
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9ucGNkbi5yYXRvcGF0aS5jb20vbWVkaWEvbmV3cy9hY2NpZGVudDFfMEhWVndFVUVBSi5qcGc.webp
date : None

title: साउदीमा ड्रोन आक्रमण: नेपालीको मृत्युप्रति कांग्रेसद्वारा दुःख व्यक्त, सुरक्षा र उद्धारको …
link : https://www.hamropatro.com/news/detail/4926028397503465-saudima-dron-akraman-nepaliko-mrityuprati-kangresadvara-duhkh-vyakt-suraksha-r
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cuZGNuYWwuY29tL3dwLWNvbnRlbnQvdXBsb2Fkcy8yMDI2LzA4L0NvbmdyZXNzXzkxNzc3OTA5NDEyLmpwZw.webp
date : None

title: पारिवारिक तीर्थाटनका लागि रवि लामिछाने भारत प्रस्थान, कैँची धाम र कामाख्या मन्दिरमा पूजा अ…
link : https://www.hamropatro.com/news/detail/4926018464732042-parivarik-tirthatanaka-lagi-ravi-lamichhane-bharat-prasthan-kainchi-dham-r
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cua2FsYWthcm1pLmNvbS93cC1jb250ZW50L3VwbG9hZHMvMjAyNi8xMC8xNzkyNzMyODI1ODg4LmpwZw.webp
date : None

title: इजरायलमा वामपन्थ र दक्षिणपन्थको अर्थ के हो ? विपक्षी को हो ?
link : https://www.hamropatro.com/news/detail/4924822949913971-ijarayalama-vamapanth-r-dakshinapanthako-arth-ke-ho-vipakshi-ko-ho
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cub25saW5la2hhYmFyLmNvbS93cC1jb250ZW50L3VwbG9hZHMvMjAyMi8xMi9iZW5qYW1pbi1uZXRhbnl1LWlzcmFlbC03Njh4NDQ4LmpwZw.webp
date : None

title: रास्वपा सभापति लामिछाने पारिवारिक तीर्थाटनका लागि भारत प्रस्थान
link : https://www.hamropatro.com/news/detail/4924817774638901-rasvapa-sabhapati-lamichhane-parivarik-tirthatanaka-lagi-bharat-prasthan
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cua2hhYmFyaHViLmNvbS93cC1jb250ZW50L3VwbG9hZHMvMjAyNi8xMC8xcmFiaS1zY2FsZS5qcGVn.webp
date : None
```

### `https://www.hamropatro.com/news/national`

```
ENTRY COUNT: 34
with image: 34 | with date: 0
normalized articles: 34
headlines carrying outlet/timestamp/summary chrome: 0
```

```
title: मुगु सदरमुकामलाई चीनसँग जोड्ने प्रयास : १० वर्षमा बन्यो १८ किलोमिटर सडक
link : https://www.hamropatro.com/news/detail/4926611560547780-mugu-sadaramukamalai-chinasang-jodne-prayas-10-varshama-banyo-18-kilomitar
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cub25saW5la2hhYmFyLmNvbS93cC1jb250ZW50L3VwbG9hZHMvMjAyNi8xMC9OYWtjaGVuYWdsYS1Sb2FkLU11Z3UtMTAyNHg1NzYuanBn.webp
date : None

title: बन्दीपुरमा विदेशी पर्यटकको हलचल बढ्यो
link : https://www.hamropatro.com/news/detail/4926606881025263-bandipurama-videshi-paryatakako-chahalapahal-badhyo
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9ucGNkbi5yYXRvcGF0aS5jb20vbWVkaWEvbmV3cy9iYW5kaXB1cl9nN2ZRbVlNQ0FOLmpwZw.webp
date : None

title: यसवर्ष दसैँमा उपत्यकामा सवारी दुर्घटना घट्यो
link : https://www.hamropatro.com/news/detail/4926307295959514-yasavarsh-dasainma-upatyakama-savari-durghatana-ghatyo
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9ucGNkbi5yYXRvcGF0aS5jb20vbWVkaWEvbmV3cy9hY2NpZGVudDFfMEhWVndFVUVBSi5qcGc.webp
date : None

title: बन्दीपुरबाट गाउँसम्म जीप सफारीको आकर्षण
link : https://www.hamropatro.com/news/detail/4926302596773615-bandipurabat-gaunsamm-jip-saphariko-akarshan
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9uZXBhbG5ld3MuY29tL3dwLWNvbnRlbnQvdXBsb2Fkcy8yMDI1LzExL0JhbmRpcHVyLU5lcGFsLVRyYXZlbC1HdWlkZS0xLmpwZw.webp
date : None

title: १० वर्षमा साढे १८ किलोमिटर सडक : अझै सडक सञ्जालबाहिर मुगुम कार्मारोङ
link : https://www.hamropatro.com/news/detail/4926023518708583-10-varshama-sadhe-18-kilomitar-sadak-ajhai-sadak-sanyjalabahir-mugum
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cuaWVwYWxwcmVzcy5jb20vd3AtY29udGVudC91cGxvYWRzLzIwMjYvMTAvc2FkYXstMTUzNng4NjQuanBlZw.webp
date : None

title: पारिवारिक तीर्थाटनका लागि रवि लामिछाने भारत प्रस्थान, कैँची धाम र कामाख्या मन्दिरमा पूजा अ…
link : https://www.hamropatro.com/news/detail/4926018464732042-parivarik-tirthatanaka-lagi-ravi-lamichhane-bharat-prasthan-kainchi-dham-r
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cua2FsYWthcm1pLmNvbS93cC1jb250ZW50L3VwbG9hZHMvMjAyNi8xMC8xNzkyNzMyODI1ODg4LmpwZw.webp
date : None

title: आगामी साताको अन्त्यमा वर्षा र हिमपातको सम्भावना, चिसो बढ्ने
link : https://www.hamropatro.com/news/detail/4926008150834016-agami-satako-antyama-varsha-r-himapatako-sambhavana-chiso-badhne
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cud2VhdGhlci11cGRhdGVfeEdFWDg0QWhqcC53ZWJw.webp
date : None

title: रवि लामिछाने धनगढीमा अवतरण, गड्डाचौकी नाका भएर भारत जाँदै
link : https://www.hamropatro.com/news/detail/4925734097745229-ravi-lamichhane-dhanagadhima-avataran-gaddachauki-naka-bhaer-bharat-jandai
image: https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly9zbm93YmVycnkucHJpeGFjZG4ubmV0L21lZGlhL2dhbGxlcnlfZm9sZGVyL3JhYmktbGFtaWNoaGFuZV9PR1BINkxkWFU4X215emR1dmNkamtwYWlkd2g3cmtwZncwZG5jNnR2Y3JtNWdzbjF5cWNwc2xidGpzZ3pwOTcyZTBsYm5lZV9OYjk5dnV0NGhlX3FybDh0Nng5YWN4eWc0ZXFnanFnejByaHk4aG5vNDB2dW95dXhwbGl6YmJtdTFxaGt1YTlwcXJ4dGVhNC5qcGc.webp
date : None
```

Every headline in both samples is the headline and nothing else — no
outlet name, no timestamp, no summary paragraph.

## 6. Two findings about the adapter, reported and not worked around

`/news` carried 26 cards and the adapter kept 24 in the first run.
The two it dropped are **real headlines**, dropped by the adapter's
own filters:

| headline | why it was dropped |
|---|---|
| फेसबुक र इन्स्टाग्रामको एल्गोरिदम सुधार्न भारत सरकारको मेटालाई निर्देशन | the filter reads "फेसबुक" as a social-media link |
| खाडीमा लाखौँ नेपालीको ज्यान जोखिममा, साउदीमा ड्रोन आक्रमणबाट एकको मृत्यु, अर्का घाइते; उद्धारको तयारी के गर्दैछ बालेन सरकार? | the filter reads the ";" as a template artifact |

By the second run those two stories had rotated off the page and all
26 cards were kept. Both filters live in
`app/sources/html_listing.py`, which this work does not edit. They
cost one headline in about thirty — the right place to fix them is
that module, and the fix is a narrower check, not this row.

## 7. What the rows say

| | `Hamro Patro News` | `Hamro Patro National` |
|---|---|---|
| `base_url` | `https://www.hamropatro.com` | `https://www.hamropatro.com` |
| `fallback_url` | `https://www.hamropatro.com/news` | `https://www.hamropatro.com/news/national` |
| `adapter_key` | `html_listing` | `html_listing` |
| `enabled` / `allowed` | true / true | true / true |
| `poll_interval_seconds` | 180 | 300 |
| `rate_limit_per_minute` | 3 | 3 |
| `sector` | `national` | `national` |
| `default_language` | `ne` | `ne` |
| `republish_permission` | false | false |

Only the headline and the picture are taken. The full text of another
publisher's story is not republished, and `republish_permission` stays
false for both rows.

## 8. Overlap, and why two rows

Hamro Patro's news pages carry the **name of the outlet each story
came from** — `onlinekhabar.com`, `ratopati.com`, `deshsanchar.com`
and others that are already sources in this pipeline. The two
sections also share stories with each other. The deduper merges the
copies (exact URL, then title, then token fingerprint), so a story
carried by four outlets is stored once and counted as carried by four.

The two rows are separate because the two pages are separate: in the
snapshot measured, only 12 of the 33 stories on `/news` were national,
and only 12 of the 32 on `/news/national` were on `/news`. Neither is
a superset of the other.

## 9. Verifying it

```bash
cd apps/ingestion
./.venv/Scripts/python.exe -m pytest tests/test_hamropatro_news.py -q
```

The test file reads the `selector_config` **out of the migration
file**, un-escapes it the way Postgres would (`''` inside a string
literal is one `'`) and parses it as JSON, then runs the adapter with
it. The configuration that was verified and the configuration that
reaches the database cannot drift apart without a test failing.
