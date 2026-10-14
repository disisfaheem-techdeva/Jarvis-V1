# Instant Grill storefront

Premium COD storefront for instantgrill.shop: Next.js 16, Tailwind 4, Motion, Postgres or file storage.

- `docs/AUDIT.md`: what was wrong with the old approach and the target spec
- `docs/ARCHITECTURE.md`: ecosystem, code map, data model, how to run and deploy
- `docs/IMAGE_BRIEF.md`: the photo set the site needs

Before launch, replace every value marked `EDIT` in `src/lib/catalog.ts` (prices, WhatsApp number, claims).

```bash
npm install && cp .env.example .env.local && npm run dev
```
