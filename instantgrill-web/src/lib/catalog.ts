/**
 * Single source of truth for everything the store sells and claims.
 *
 * Every price, claim and image path on the site is read from here, and the
 * order API prices carts from here too, so the client can never set its own
 * price. Values marked EDIT are placeholders: replace them with the real
 * product facts before launch. Do not publish a claim the product cannot back.
 */

export type ImageSlot = {
  /** Path under /public, e.g. "/images/hero.webp". null renders a labelled placeholder. */
  src: string | null;
  alt: string;
  /** What the photo must show. Also used as the generation brief in docs/IMAGE_BRIEF.md. */
  brief: string;
};

export type Bundle = {
  id: string;
  name: string;
  /** One line under the name: what is in the box. */
  contents: string;
  /** Price in whole rupees. EDIT */
  price: number;
  /** Struck-through reference price, only if it is a real previous price. EDIT */
  compareAt?: number;
  highlight?: boolean;
};

export const brand = {
  name: "Instant Grill",
  domain: "instantgrill.shop",
  tagline: "Charcoal flavour without the wait.",
  /** International format, digits only, used for wa.me links. EDIT */
  whatsapp: "923000000000",
  email: "hello@instantgrill.shop",
  currency: "PKR",
};

export const shipping = {
  /** Flat delivery fee in rupees. EDIT */
  fee: 250,
  /** Orders at or above this subtotal ship free. EDIT */
  freeAbove: 10000,
  /** Shown on product and checkout. EDIT */
  eta: "2 to 5 working days across Pakistan",
  replacementDays: 7,
};

export const product = {
  slug: "instant-grill",
  name: "Instant Grill",
  summary:
    "A compact charcoal grill built to get coals cooking-hot fast, then pack away into one carry case.",
  bundles: [
    {
      id: "solo",
      name: "The Grill",
      contents: "Instant Grill, grate, ash tray, carry case",
      price: 12999,
    },
    {
      id: "cookout",
      name: "Cookout Kit",
      contents: "The Grill plus tongs, skewer set and heat gloves",
      price: 15499,
      compareAt: 16997,
      highlight: true,
    },
    {
      id: "family",
      name: "Family Pack",
      contents: "Two grills plus the full accessory kit",
      price: 27999,
      compareAt: 30997,
    },
  ] satisfies Bundle[],
  maxQtyPerLine: 5,
};

export const images = {
  hero: {
    src: null,
    alt: "Instant Grill with glowing coals and skewers on a rooftop at dusk",
    brief:
      "Hero product shot. Instant Grill three-quarter view, lid off, coals glowing orange, seekh kebabs on the grate, faint smoke. Rooftop at blue-hour dusk, Lahore skyline soft in background. Dark, cinematic, warm rim light from the coals. 4:5 portrait, product fills 60% of frame.",
  },
  fastStart: {
    src: null,
    alt: "Coals catching inside the grill's chimney chamber",
    brief:
      "Macro close-up looking down into the grill: charcoal catching, bright orange edges, sparks lifting. Black background. 16:10 landscape.",
  },
  portable: {
    src: null,
    alt: "Instant Grill packed in its carry case in a car boot",
    brief:
      "Grill packed in its carry case sitting in an open car boot next to a cooler, mountain road in soft focus (Murree or Naran feel). Golden hour. 4:5 portrait.",
  },
  clean: {
    src: null,
    alt: "Hand sliding out the ash tray after cooking",
    brief:
      "Close-up of a hand sliding out the ash tray from under the grill, ash contained, clean stone counter. Even soft light, dark tones. 1:1 square.",
  },
  family: {
    src: null,
    alt: "Family gathered around the grill on a terrace",
    brief:
      "Pakistani family of four around the grill on a home terrace at night, string lights, tikka on the grate, candid laughter, faces partly turned. Warm, documentary feel. 16:9 landscape.",
  },
  build: {
    src: null,
    alt: "Detail of the grill's steel body and vents",
    brief:
      "Studio detail of the steel body and air vents, raking side light showing the finish, charcoal-black seamless backdrop. 1:1 square.",
  },
  howLoad: {
    src: null,
    alt: "Charcoal being poured into the grill",
    brief: "Top-down: charcoal being poured into the empty grill. Dark slate surface. 4:3.",
  },
  howLight: {
    src: null,
    alt: "Flame lit at the base of the grill",
    brief: "Low angle: a match lighting the starter at the base vent, first flames. 4:3.",
  },
  howGrill: {
    src: null,
    alt: "Chicken tikka sizzling on the grate",
    brief: "Close-up: chicken tikka sizzling on the grate, char marks, light smoke. 4:3.",
  },
} satisfies Record<string, ImageSlot>;

/**
 * Why Instant Grill. Five reasons, five bento cells (see WhyBento).
 * Keep claims that the product can prove. EDIT the copy to the real specs.
 */
export const reasons = [
  {
    key: "fast",
    title: "Coals ready in minutes, not an hour",
    body: "A chimney-style airflow pulls air through the charcoal so it lights evenly and gets hot quickly.",
    image: "fastStart",
  },
  {
    key: "portable",
    title: "Packs into one case",
    body: "Rooftop, lawn, picnic or the Naran trip. It travels in the boot and sets up in a minute.",
    image: "portable",
  },
  {
    key: "clean",
    title: "Ash stays in the tray",
    body: "Slide the tray out, empty it, done. No ash on the floor.",
    image: "clean",
  },
  {
    key: "flavour",
    title: "Real charcoal taste",
    body: "Gas and electric grills can't give you that smoky tikka and seekh flavour. Charcoal can.",
    image: "family",
  },
  {
    key: "build",
    title: "Built to last for years",
    body: "Heavy steel body and a grate that won't warp. Backed by a replacement guarantee.",
    image: "build",
  },
] as const;

export const steps = [
  { title: "Load", body: "Fill the chamber with charcoal. No pyramid building.", image: "howLoad" },
  { title: "Light", body: "Light the base. The airflow does the rest while you prep.", image: "howLight" },
  { title: "Grill", body: "Grate on, food on. Tikka, seekh, corn, burgers.", image: "howGrill" },
] as const;

export const comparison = {
  us: [
    "Coals ready while you marinate",
    "Ash contained in a tray",
    "Fits in the car boot",
    "Real charcoal flavour",
  ],
  them: [
    "Fanning coals for 30 minutes or more",
    "Ash and embers on the floor",
    "Heavy and stays in one place",
    "Gas and electric taste flat",
  ],
};

export const faqs = [
  {
    q: "Can I pay cash on delivery?",
    a: "Yes. Cash on delivery is available across Pakistan. We call or WhatsApp to confirm your order before dispatch.",
  },
  {
    q: "How long does delivery take?",
    a: `Usually ${shipping.eta}. You get a tracking number on WhatsApp once it ships.`,
  },
  {
    q: "What if it arrives damaged?",
    a: `Send us a photo on WhatsApp within ${shipping.replacementDays} days and we replace it.`,
  },
  {
    q: "What fuel does it use?",
    a: "Regular lump charcoal or briquettes from any store.",
  },
  {
    q: "Can I use it indoors?",
    a: "No. Like every charcoal grill, use it outdoors or in an open, well-ventilated space only.",
  },
];

/**
 * Real customer reviews only. The reviews section stays hidden while this is
 * empty, so the site never shows invented testimonials.
 */
export const reviews: { name: string; city: string; text: string; rating: number }[] = [];

export function findBundle(id: string): Bundle | undefined {
  return product.bundles.find((b) => b.id === id);
}
