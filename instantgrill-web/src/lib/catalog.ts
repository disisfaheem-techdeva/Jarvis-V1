/**
 * Single source of truth for the menu, prices, facts and image slots.
 * Every page and the order API read from here; the server prices every order
 * from this file, so the browser can never set its own price.
 * Values marked [TO CONFIRM] are waiting on the owner.
 */

export type ImageSlot = {
  /** Path under /public, e.g. "/images/hero.webp". null renders a placeholder. */
  src: string | null;
  alt: string;
  /** What the photo must show (see docs/IMAGE_BRIEF.md). */
  brief: string;
};

export type Category = "chicken" | "beef" | "mutton" | "mix" | "family" | "deal" | "extra" | "drink";

export type Product = {
  slug: string;
  name: string;
  category: Category;
  /** Whole rupees. */
  price: number;
  pieces?: number;
  serves?: number;
  /** For mix, family and deal boxes: what is inside, by slug. Used for savings maths. */
  contains?: string[];
  description: string;
  tags?: ("boneless" | "kabab" | "premium")[];
  soldOut?: boolean;
  image: ImageSlot;
};

export const brand = {
  name: "Instant Grill",
  domain: "instantgrill.shop",
  positioning: "Pakistan's first ready-to-grill BBQ box",
  whatsapp: "923354693665",
  phoneDisplay: "0335 4693665",
  email: "info@instantgrill.shop",
  address: "Orange Square, Baghbanpura, near UET, Lahore",
  instagram: "https://www.instagram.com/instant_grill.shop",
  tiktok: "https://www.tiktok.com/@instantgrill.shop",
  facebook: "https://www.facebook.com/search/top?q=Instant%20Grill", // [TO CONFIRM] exact Page URL
  currency: "PKR",
  hoursDisplay: "Open daily 12 PM – 12 AM. Morning delivery 10 AM – 12 PM.",
};

export const delivery = {
  city: "Lahore",
  /** Rupees, or null until confirmed. While null, checkout says the fee is confirmed on WhatsApp. [TO CONFIRM] */
  fee: null as number | null,
  /** [TO CONFIRM] the areas you actually deliver to. "Other" lets the team confirm by phone. */
  areas: [
    "Baghbanpura",
    "Shalimar",
    "Mughalpura",
    "Harbanspura",
    "Lahore Cantt",
    "Gulberg",
    "DHA",
    "Model Town",
    "Garden Town",
    "Faisal Town",
    "Johar Town",
    "Allama Iqbal Town",
    "Township",
    "Wapda Town",
    "Bahria Town",
    "Valencia",
    "Samanabad",
    "Shadman",
    "Walled City",
    "Other (we will confirm)",
  ],
};

export const BOX_INCLUDES = [
  "~850 g marinated meat",
  "Disposable grill + coal",
  "Tong",
  "4 pita breads",
  "Hummus",
  "Aloo Bukhara or Imli chutney",
  "Fresh or Russian salad",
];

export const CHUTNEYS = ["Aloo Bukhara", "Imli"] as const;
export const SALADS = ["Fresh", "Russian"] as const;
export type Chutney = (typeof CHUTNEYS)[number];
export type Salad = (typeof SALADS)[number];

const img = (alt: string, brief: string): ImageSlot => ({ src: null, alt, brief });
const boxImg = (name: string) =>
  img(
    `Open Instant Grill box with ${name} on the grill`,
    `Open white Instant Grill box on dark surface, ${name} on the built-in grill, pita, hummus and salad tubs around it. Use the owner's real box photo. 4:3.`,
  );

const BOX_DESC = "Serves 2. About 850 g marinated meat with the grill, coal, tong, pita, hummus, chutney and salad.";

export const products: Product[] = [
  // Chicken
  { slug: "chicken-tikka", name: "Chicken Tikka", category: "chicken", price: 3000, serves: 2, tags: ["boneless"], description: BOX_DESC, image: boxImg("chicken tikka") },
  { slug: "chicken-kabab", name: "Chicken Kabab", category: "chicken", price: 3100, pieces: 6, serves: 2, tags: ["kabab"], description: BOX_DESC, image: boxImg("chicken kababs") },
  { slug: "chicken-shashlik", name: "Chicken Shashlik", category: "chicken", price: 3200, serves: 2, tags: ["boneless"], description: BOX_DESC, image: boxImg("chicken shashlik skewers") },
  { slug: "malai-boti", name: "Malai Boti", category: "chicken", price: 3300, serves: 2, tags: ["boneless"], description: BOX_DESC, image: boxImg("malai boti") },
  // Beef
  { slug: "beef-seekh-kabab", name: "Beef Seekh Kabab", category: "beef", price: 3500, serves: 2, tags: ["kabab"], description: BOX_DESC, image: boxImg("beef seekh kababs") },
  { slug: "beef-bihari-boti", name: "Beef Bihari Boti", category: "beef", price: 3800, serves: 2, tags: ["boneless"], description: BOX_DESC, image: boxImg("beef bihari boti") },
  { slug: "rib-eye-tikka", name: "Rib Eye Tikka", category: "beef", price: 3900, serves: 2, tags: ["boneless", "premium"], description: BOX_DESC, image: boxImg("rib eye tikka") },
  // Mutton
  { slug: "mutton-seekh-kabab", name: "Mutton Seekh Kabab", category: "mutton", price: 4200, serves: 2, tags: ["kabab"], description: BOX_DESC, image: boxImg("mutton seekh kababs") },
  { slug: "reshmi-boti", name: "Mutton Reshmi Boti", category: "mutton", price: 4800, serves: 2, tags: ["boneless", "premium"], description: BOX_DESC, image: boxImg("mutton reshmi boti") },
  { slug: "namkeen-boti", name: "Mutton Namkeen Boti", category: "mutton", price: 4800, serves: 2, tags: ["premium"], description: BOX_DESC, image: boxImg("mutton namkeen boti") },
  { slug: "mutton-champ", name: "Mutton Champ", category: "mutton", price: 4800, pieces: 6, serves: 2, tags: ["premium"], description: BOX_DESC, image: boxImg("mutton champ") },
  // Mix (half/half)
  { slug: "mix-malai-tikka", name: "Malai Boti + Chicken Tikka", category: "mix", price: 3150, serves: 2, contains: ["malai-boti", "chicken-tikka"], description: "Half and half. " + BOX_DESC, image: boxImg("malai boti and chicken tikka") },
  { slug: "mix-tikka-beef-seekh", name: "Chicken Tikka + Beef Seekh", category: "mix", price: 3250, serves: 2, contains: ["chicken-tikka", "beef-seekh-kabab"], description: "Half and half. " + BOX_DESC, image: boxImg("chicken tikka and beef seekh") },
  { slug: "mix-beef-seekh-bihari", name: "Beef Seekh + Bihari Boti", category: "mix", price: 3650, serves: 2, contains: ["beef-seekh-kabab", "beef-bihari-boti"], description: "Half and half. " + BOX_DESC, image: boxImg("beef seekh and bihari boti") },
  { slug: "mix-malai-mutton-seekh", name: "Malai Boti + Mutton Seekh", category: "mix", price: 3750, serves: 2, contains: ["malai-boti", "mutton-seekh-kabab"], description: "Half and half. " + BOX_DESC, image: boxImg("malai boti and mutton seekh") },
  { slug: "mix-mutton-seekh-namkeen", name: "Mutton Seekh + Namkeen Boti", category: "mix", price: 4500, serves: 2, contains: ["mutton-seekh-kabab", "namkeen-boti"], description: "Half and half. " + BOX_DESC, image: boxImg("mutton seekh and namkeen boti") },
  { slug: "trio-mix", name: "Trio Mix", category: "mix", price: 3600, serves: 2, contains: ["chicken-tikka", "beef-seekh-kabab", "mutton-seekh-kabab"], description: "Chicken Tikka, Beef Seekh and Mutton Seekh. " + BOX_DESC, image: boxImg("chicken tikka, beef seekh and mutton seekh") },
  // Family (2 boxes, serves 4)
  { slug: "family-chicken", name: "Family Chicken", category: "family", price: 6000, serves: 4, contains: ["chicken-tikka", "malai-boti"], description: "Two boxes: Chicken Tikka and Malai Boti. Serves 4.", image: boxImg("two boxes of chicken tikka and malai boti") },
  { slug: "family-chicken-beef", name: "Family Chicken + Beef", category: "family", price: 6200, serves: 4, description: "Two boxes, one chicken and one beef. Serves 4.", image: boxImg("a chicken box and a beef box") },
  { slug: "family-beef", name: "Family Beef", category: "family", price: 6900, serves: 4, contains: ["beef-seekh-kabab", "beef-bihari-boti"], description: "Two boxes: Beef Seekh and Bihari Boti. Serves 4.", image: boxImg("two boxes of beef seekh and bihari boti") },
  { slug: "family-chicken-mutton", name: "Family Chicken + Mutton", category: "family", price: 7100, serves: 4, description: "Two boxes, one chicken and one mutton. Serves 4.", image: boxImg("a chicken box and a mutton box") },
  { slug: "family-mutton", name: "Family Mutton", category: "family", price: 8500, serves: 4, contains: ["mutton-seekh-kabab", "reshmi-boti"], description: "Two boxes: Mutton Seekh and Reshmi Boti. Serves 4.", image: boxImg("two boxes of mutton seekh and reshmi boti") },
  // Deals
  { slug: "dawat-deal", name: "Dawat Deal", category: "deal", price: 10000, serves: 6, contains: ["chicken-tikka", "beef-seekh-kabab", "mutton-seekh-kabab"], description: "Three boxes: Chicken Tikka, Beef Seekh and Mutton Seekh. Serves 6.", image: boxImg("three boxes laid out for a dawat") },
  { slug: "party-deal", name: "Party Deal", category: "deal", price: 12500, serves: 8, contains: ["chicken-tikka", "chicken-tikka", "beef-seekh-kabab", "mutton-seekh-kabab"], description: "Four boxes: 2 Chicken Tikka, Beef Seekh and Mutton Seekh. Serves 8.", image: boxImg("four boxes on a rooftop table for a party") },
  // Extras
  { slug: "extra-chicken-skewer", name: "Extra Chicken Skewer", category: "extra", price: 550, description: "One extra marinated chicken skewer.", image: img("Marinated chicken skewer", "Single raw marinated chicken skewer on dark slate. 1:1.") },
  { slug: "extra-beef-skewer", name: "Extra Beef Skewer", category: "extra", price: 650, description: "One extra marinated beef skewer.", image: img("Marinated beef skewer", "Single raw marinated beef skewer on dark slate. 1:1.") },
  { slug: "extra-mutton-skewer", name: "Extra Mutton Skewer", category: "extra", price: 800, description: "One extra marinated mutton skewer.", image: img("Marinated mutton skewer", "Single raw marinated mutton skewer on dark slate. 1:1.") },
  // Drinks [TO CONFIRM] brands/flavours
  { slug: "drink-375", name: "Soft Drink 375 ml", category: "drink", price: 100, description: "Chilled soft drink.", image: img("Soft drink can", "Chilled soft drink, 375 ml. 1:1.") },
  { slug: "drink-500", name: "Soft Drink 500 ml", category: "drink", price: 150, description: "Chilled soft drink.", image: img("Soft drink bottle 500 ml", "Chilled soft drink, 500 ml. 1:1.") },
  { slug: "drink-1l", name: "Soft Drink 1 L", category: "drink", price: 200, description: "Chilled soft drink.", image: img("Soft drink bottle 1 litre", "Chilled soft drink, 1 litre. 1:1.") },
  { slug: "drink-1-5l", name: "Soft Drink 1.5 L", category: "drink", price: 250, description: "Chilled soft drink.", image: img("Soft drink bottle 1.5 litre", "Chilled soft drink, 1.5 litre. 1:1.") },
];

export const CATEGORIES: { id: Category; label: string }[] = [
  { id: "chicken", label: "Chicken" },
  { id: "beef", label: "Beef" },
  { id: "mutton", label: "Mutton" },
  { id: "mix", label: "Mix Boxes" },
  { id: "family", label: "Family" },
  { id: "deal", label: "Deals" },
  { id: "extra", label: "Extras" },
  { id: "drink", label: "Drinks" },
];

/** Boxes come with a chutney and salad choice; extras and drinks do not. */
export function isBox(p: Product): boolean {
  return p.category !== "extra" && p.category !== "drink";
}

export function findProduct(slug: string): Product | undefined {
  return products.find((p) => p.slug === slug);
}

/** Saving versus buying the contained boxes separately. null when contents are not fixed. */
export function savingFor(p: Product): number | null {
  if (!p.contains) return null;
  const separate = p.contains.reduce((s, slug) => s + (findProduct(slug)?.price ?? 0), 0);
  // Half/half mix boxes are one box, not two, so "separately" does not apply.
  if (p.category === "mix") return null;
  return separate > p.price ? separate - p.price : null;
}

export const BESTSELLERS = ["chicken-tikka", "malai-boti", "beef-bihari-boti", "trio-mix"];

export const images = {
  hero: img(
    "Open Instant Grill box with skewers on the built-in grill and glowing coals",
    "HERO: the owner's real open-box photo. White Instant Grill box on dark background, skewers on the grill, coals glowing, pita and sides around. 4:5.",
  ),
  everything: img(
    "Everything that comes in an Instant Grill box laid out",
    "Flat lay on dark surface: box, disposable grill, coal, tong, marinated meat, 4 pita, hummus, chutney, salad. Real photo. 16:10.",
  ),
  rooftop: img("Friends grilling an Instant Grill box on a Lahore rooftop", "Rooftop at dusk, box on a ledge, skewers on the grill, string lights. 4:5."),
  noMess: img("Hands lifting the disposable grill out of the box after cooking", "Close-up: cooked skewers, the disposable grill tray, a clean table. 1:1."),
  fresh: img("Marinated meat inside the box before grilling", "Close-up of marinated tikka in its tray inside the box. 1:1."),
  dawat: img("Family dawat with several Instant Grill boxes on a terrace", "Night terrace, family around 3 boxes, warm light. 16:9."),
  stepOrder: img("Ordering an Instant Grill box on a phone", "Hand holding a phone showing WhatsApp chat with Instant Grill. 4:3."),
  stepDeliver: img("Rider handing over a sealed Instant Grill box", "Sealed white box being handed over at a door. 4:3."),
  stepLight: img("Lighting the coal in the box's disposable grill", "Match lighting the coal in the grill tray, first flames. 4:3."),
  stepGrill: img("Skewers sizzling on the Instant Grill", "Close-up of tikka and seekh sizzling on the grill, light smoke. 4:3."),
} satisfies Record<string, ImageSlot>;

export const reasons = [
  { key: "everything", title: "Everything in one box", body: "Grill, coal, tong, marinated meat, pita, hummus, chutney and salad. Nothing to buy, nothing to prep.", image: "everything" },
  { key: "anywhere", title: "BBQ anywhere", body: "Rooftop, lawn, farmhouse, cricket night or a camping trip. Open the box and light it.", image: "rooftop" },
  { key: "fresh", title: "Marinated fresh", body: "Marinated fresh every day in our Baghbanpura kitchen.", image: "fresh" },
  { key: "nomess", title: "No mess after", body: "The grill is disposable. Let the coals cool, then throw it away.", image: "noMess" },
  { key: "dawat", title: "Made for sharing", body: "One box serves 2. Family boxes, Dawat and Party deals feed 4 to 8.", image: "dawat" },
] as const;

export const steps = [
  { title: "Order", body: "Pick your box on the site or on WhatsApp. Pay cash on delivery.", image: "stepOrder" },
  { title: "Delivered in the box", body: "Our rider brings it within 6 hours, or in the morning slot.", image: "stepDeliver" },
  { title: "Light the coal", body: "Light it and wait about 10 minutes until the coals glow.", image: "stepLight" },
  { title: "Grill and eat", body: "Skewers on, turn with the tong, serve with pita and sides.", image: "stepGrill" },
] as const;

export const grillGuide = [
  "Place the box on a flat, heat-safe surface outdoors or in the open air.",
  "Light the coal and wait about 10 minutes until it glows and stops flaming.",
  "Put the skewers on and turn them with the tong until cooked through.",
  "Let the coals cool completely before you throw the grill away.",
];

export const faqs = [
  { q: "What's in the box?", a: `${BOX_INCLUDES.join(", ")}. One box serves 2.` },
  { q: "How fast is delivery?", a: "We deliver within 6 hours of your order. Orders placed late evening or after midnight are delivered the next morning between 10 AM and 12 PM." },
  { q: "Where do you deliver?", a: "Across Lahore by our own riders. Pick your area at checkout; if it is not listed, choose Other and we will confirm." },
  { q: "Can I use it indoors?", a: "No. Use it outdoors or in the open air only, away from children, and let the coals cool before disposal." },
  { q: "How do I store it if I am not grilling right away?", a: "Keep it in the fridge and grill it the same day." },
  { q: "How do I pay?", a: "Cash on delivery. JazzCash and Easypaisa are coming soon." },
  { q: "Can I change or cancel my order?", a: `Message us on WhatsApp at ${brand.phoneDisplay} with your order number before it is out for delivery.` },
  { q: "Do you do bulk or event orders?", a: "Yes. The Dawat Deal serves 6 and the Party Deal serves 8. For bigger events, message us on WhatsApp." },
];

/** Real customer reviews only. The section stays hidden while this is empty. */
export const reviews: { name: string; area: string; text: string; rating: number }[] = [];
