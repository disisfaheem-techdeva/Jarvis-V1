# Instant Grill

Pakistan's first ready-to-grill BBQ box, Lahore. Next.js 16, Tailwind 4, Motion, Postgres or file storage.

- `src/lib/catalog.ts`: menu, prices, contact, delivery areas (single source of truth)
- `src/lib/delivery.ts`: delivery slot rules
- `docs/AUDIT.md`: what was built, QA results and the [TO CONFIRM] list
- `docs/ARCHITECTURE.md`: system map, code map, data model, run and deploy
- `docs/IMAGE_BRIEF.md`: which photo goes where

```bash
npm install && cp .env.example .env.local && npm run dev
npm test && npm run typecheck && npm run lint && npm run build
```
