# Brief for Cowork: techdeva.net SEO, content and AI-assistant visibility

**Owner:** Faheem (TechDeva). **Date:** 1 October 2026.
**Site:** techdeva.net (WordPress on Hostinger). The product site `portal.techdeva.net` is already done (see "Already done" at the end); do not change it.

## Rules
- Work only on techdeva.net (WordPress). Do not touch the server at 72.61.203.194, crm.techdeva.net or anything WhatsApp/Hermes.
- Do not change prices. Use exactly the prices below.
- Take a full backup (Hostinger backup or UpdraftPlus) before starting, and clear the cache after each page.
- Never paste passwords or keys into chats or pages.
- When you finish each section, tick it and note the page URL.

---

## 1. Site-wide setup (do first)

- [ ] **SEO plugin:** check Rank Math works (it was broken before). If it does not, remove it and install Yoast SEO. Only one SEO plugin.
- [ ] **Site title:** `TechDeva`. **Tagline:** `AI business operating system for clinics, salons, gyms and academies`.
- [ ] **Search engines:** Settings → Reading: "Discourage search engines" must be OFF.
- [ ] **Permalinks:** "Post name" (`/sample-post/`).
- [ ] **XML sitemap:** on (from the SEO plugin). Submit `https://techdeva.net/sitemap_index.xml` (or the plugin's address) in:
  - Google Search Console (verify the domain if not done);
  - **Bing Webmaster Tools** (important: ChatGPT's search uses Bing). Import from Google Search Console to save time; turn on IndexNow.
- [ ] **robots.txt:** allow everything except `/wp-admin/`, and name the AI crawlers so they are clearly allowed:
  ```
  User-agent: *
  Allow: /
  Disallow: /wp-admin/
  Allow: /wp-admin/admin-ajax.php

  User-agent: GPTBot
  User-agent: OAI-SearchBot
  User-agent: ChatGPT-User
  User-agent: ClaudeBot
  User-agent: Claude-SearchBot
  User-agent: PerplexityBot
  User-agent: Google-Extended
  User-agent: Bingbot
  Allow: /

  Sitemap: https://techdeva.net/sitemap_index.xml
  ```
- [ ] **llms.txt:** upload a file at `https://techdeva.net/llms.txt` with the same text as `https://portal.techdeva.net/llms.txt` (copy it from there). It tells AI assistants what TechDeva is, in plain words.
- [ ] **Schema (structured data):** in the SEO plugin set Organization = "TechDeva AI Solutions", logo, Lahore, Pakistan, phone, email, social profiles (Facebook, Instagram, LinkedIn, YouTube).
- [ ] **Favicon and social image:** site icon set; a default social sharing image (1200×630) with the TechDeva logo and "AI business operating system".
- [ ] **Speed:** one cache plugin (LiteSpeed Cache on Hostinger), images WebP, lazy-load on. Target: PageSpeed mobile 70+ on the home page.
- [ ] **Google Business Profile:** create or claim "TechDeva AI Solutions" (Software company, Lahore), same name, address and phone as the site footer, link to techdeva.net.

## 2. Every page: the checklist

For **each page and each blog post**:
- [ ] One H1 that says what the page is (not the site name).
- [ ] SEO title under 60 characters, with the main search phrase first, ending `| TechDeva`.
- [ ] Meta description 140–155 characters: what it is, who it is for, one reason to act.
- [ ] Short, readable URL (no dates or IDs).
- [ ] H2/H3 headings in order; paragraphs of 2–4 sentences.
- [ ] Every image has alt text that describes it (for example "WhatsApp AI agent booking an appointment for a clinic").
- [ ] At least 3 internal links: to Pricing, to one feature page, to Book a demo.
- [ ] A clear button: "Start 7-day free trial" → `https://api.techdeva.net/` and "Book a demo" → `https://portal.techdeva.net/demo`.
- [ ] No broken links (use a broken-link checker plugin once, then remove it).
- [ ] Mobile view checked.

Start with an **inventory**: list every page and post URL in a sheet with columns *URL, H1, SEO title, meta description, word count, fixed (yes/no)*. Send the sheet to Faheem at the end.

## 3. Main pages: ready-to-use titles and descriptions

| Page | SEO title | Meta description |
|---|---|---|
| Home | TechDeva: clinic & salon software with a WhatsApp AI agent | One dashboard for clinics, salons, gyms and academies: bookings, bills, stock, team and a WhatsApp AI agent that answers and books 24/7. 7-day free trial. |
| AIBOS / product | TechDeva AIBOS: your AI business operating system | Bookings, invoices, customers, stock, team, WhatsApp AI agent, social posting, ad results and a growth advisor in one place. See every feature. |
| Pricing | TechDeva pricing: Starter, Growth and Pro | From PKR 19,999 a month (USD 99 abroad). Replaces tools that cost up to PKR 170,000 a month. 7-day free trial, no setup fee. |
| Clinics | Clinic management software with WhatsApp AI \| TechDeva | For aesthetic, skin and dental clinics: appointments, patient history, bills and a WhatsApp AI receptionist that books patients 24/7. |
| Salons | Salon booking software with WhatsApp AI \| TechDeva | Bookings, staff, stock and a WhatsApp AI agent that answers clients and fills your calendar, for salons and spas. |
| Gyms | Gym management software with WhatsApp AI \| TechDeva | Members, payments, staff and a WhatsApp AI agent that answers enquiries and books trials for gyms and fitness studios. |
| Academies | Academy management software with WhatsApp AI \| TechDeva | Enquiries, enrolments, fees and a WhatsApp AI agent that answers students 24/7, for training academies. |
| About | About TechDeva: AI for service businesses, from Lahore | TechDeva is built in Lahore and runs in production at a doctor-led aesthetic clinic. Meet the team and why we built it. |
| Contact / Demo | Book a TechDeva demo | See TechDeva on your own business. Tell us about it and we will call or message you on WhatsApp. |

If an industry page does not exist yet, create it (section 5 has the feature copy to reuse).

## 4. Pricing section (add to the Pricing page, or link the menu to portal)

Simplest: point the **Pricing** menu item to `https://portal.techdeva.net/pricing` (it already shows the packages, and switches to USD for visitors outside Pakistan). If you also want the packages on techdeva.net, use exactly this:

**Pick your package. Drop your other tools.**

| | Starter | Growth (most popular) | Pro |
|---|---|---|---|
| Pakistan | PKR 19,999 / month | PKR 49,999 / month | PKR 64,999 / month |
| Outside Pakistan | USD 99 / month | USD 199 / month | USD 299 / month |
| WhatsApp numbers | 1 | 2 | 5 |
| AI chats per month | 300 | 1,500 | 4,000 |
| WhatsApp AI sales agent, 24/7 | ✓ | ✓ | ✓ |
| Bookings, calendar & customer CRM | ✓ | ✓ | ✓ |
| Invoices, payments & profit/loss | ✓ | ✓ | ✓ |
| Stock & low-stock alerts | ✓ | ✓ | ✓ |
| Team logins, attendance & HR | ✓ | ✓ | ✓ |
| Facebook & Instagram posting | | ✓ | ✓ |
| Live ad results, Meta & Google | | ✓ | ✓ |
| Jarvis, your AI business assistant | | ✓ | ✓ |
| Growth advisor & ad autopilot | | | ✓ |
| What the same tools cost separately | PKR 59,000 (USD 213) | PKR 109,900 (USD 397) | PKR 170,600 (USD 616) |

Under the table: *Every package: 7-day free trial, no setup fee, onboarding and WhatsApp setup included. Tool prices are 2026 monthly list prices (WATI, respond.io, Vagaro, Zoho Books, Zoho Inventory, Metricool, DashThis, Connecteam, ChatGPT Plus, Bïrch), converted at PKR 277 per US dollar.*

Showing USD abroad on WordPress: either show both currencies (as above), or add a small currency switcher; do not guess prices, use only these.

## 5. Features: headline and description for each (use on the product page)

Each feature should have its own H2 and short paragraph on the AIBOS/product page. Features that have their own page get a link from here.

1. **WhatsApp AI sales agent, 24/7.** Answers every enquiry on your own WhatsApp number in your customer's language, qualifies them and books the appointment. It hands the chat to you when a person should answer. Bigger packages cover several numbers.
2. **Appointments & calendar.** Book by service and staff member, take part payments, and see every balance from the real bill.
3. **Customer history.** Every visit, payment, package, note and WhatsApp chat for each customer, in one place.
4. **Invoices & money.** Bills you can print, payments, expenses, daily closing, and profit and loss.
5. **Stock & low-stock alerts.** Every product movement recorded, with an alert before you run out.
6. **Team, PIN logins & HR.** Staff records, attendance and HR. Each person logs in with their own PIN and sees only their part; prices and deletions stay with the owner.
7. **Facebook & Instagram posting.** Write or dictate a post, add your photo, and publish or schedule it. Nothing goes public without your approval.
8. **Live ad results.** Spend, leads, WhatsApp chats and cost per result from your Meta and Google ads, next to the bookings they brought.
9. **Jarvis, your AI assistant.** Ask about your figures, or tell it to book, reschedule or add an expense, just by talking.
10. **Growth advisor.** Every week it reads your own numbers and tells you what to fix (unpaid bills, no-shows, costly ads) and what to do next to grow.
11. **Ad autopilot.** Pauses campaigns that cost more than your limit, and writes new ones (picture, headline, text, hashtags, keywords), launching only within the daily budget you set.
12. **Your data stays yours.** Every business is kept separate. Your AI works for you and never spends beyond the limits you set.

Do **not** mention AI video (not built yet).

## 6. Blog

For **every existing post**: apply the checklist in section 2, plus:
- [ ] Update the date if you change the content substantially.
- [ ] Add an author box (Faheem, founder of TechDeva) with a one-line bio.
- [ ] End with a short "How TechDeva helps" paragraph and the two buttons.
- [ ] Add 3–5 questions and answers at the end (FAQ block with FAQ schema) that match what people ask.
- [ ] Merge or redirect (301) thin or duplicate posts instead of leaving them.

**New posts to write** (one a week; each 900–1,500 words, practical, with real examples; Pakistani context first):
1. Best clinic management software in Pakistan (2026): what to look for
2. How a WhatsApp AI receptionist books patients while you sleep
3. WhatsApp chatbot for clinics: WATI vs respond.io vs TechDeva
4. How much do lost WhatsApp enquiries cost a clinic? A simple calculation
5. Salon booking software in Pakistan: a buyer's checklist
6. Cutting no-shows at your clinic: reminders that work
7. Facebook ads for clinics in Lahore: why cost per lead goes up, and what to pause
8. Dental clinic software with WhatsApp: a guide for Pakistani dentists
9. Gym enquiries on WhatsApp: turning messages into paid members
10. One dashboard vs eight apps: what a service business really pays for software

Each new post needs its own SEO title, description, one H1, internal links to Pricing and a feature, and an FAQ block.

## 7. Getting recommended by ChatGPT, Claude, Perplexity and Gemini

AI assistants recommend what they can find and trust on the open web. Do these:
1. **Be readable to them:** robots.txt and llms.txt as in section 1; pages in plain text (not text inside images); a clear one-line description used everywhere:
   *"TechDeva is a business operating system for clinics, salons, gyms and academies, with a WhatsApp AI agent that answers and books customers 24/7."*
2. **Be on Bing and Google:** both consoles set up and the sitemap submitted (section 1).
3. **Answer the exact questions people ask:** the comparison and "best software in Pakistan" posts in section 6, and FAQ blocks with FAQ schema.
4. **Be listed where AI assistants look:** create free profiles with the same description, logo and prices on:
   - G2, Capterra and GetApp (software review sites);
   - SoftwareSuggest (Pakistan section);
   - Product Hunt;
   - Crunchbase;
   - LinkedIn company page;
   - Clutch.

   Then ask happy clients (starting with The Beauty Aesthetic & Lounge) for honest reviews there.
5. **Be mentioned by others:** pitch a short founder story to Pakistani tech media (TechJuice, ProPakistani, Dawn Business). Answer real questions on Reddit and Quora about clinic or salon software, saying openly that you work at TechDeva.
6. **Show it working:** 2–3 short YouTube demos ("WhatsApp AI agent booking a clinic appointment") with full descriptions and captions, embedded on the product page.
7. **Keep facts the same everywhere:** name, address, phone, prices and the one-line description must match on every site.
8. **Check monthly:** ask ChatGPT, Claude, Perplexity and Gemini "best clinic software in Pakistan with WhatsApp AI" and "WhatsApp AI receptionist for salons". Note whether TechDeva appears and which sources they cite, and send Faheem the notes.

This takes weeks to months to show up in answers. Being on review sites and in articles usually matters more than the site itself.

## 8. Report back to Faheem
- The page/post inventory sheet with what was fixed.
- Screenshots of Google Search Console and Bing Webmaster Tools showing the sitemap accepted.
- The list of directory profiles created, with links.
- Anything you could not do, and why.

---

### Already done on portal.techdeva.net (for reference, do not redo)
- New pricing page with the three packages, PKR in Pakistan and USD elsewhere, and an FAQ.
- Titles, descriptions and canonical links on every page; private pages hidden from search.
- sitemap.xml, robots.txt allowing AI crawlers, llms.txt, and structured data (Organization, SoftwareApplication, Product offers, FAQ).
- Home and Platform pages now list every feature, including the growth advisor and ad autopilot.
