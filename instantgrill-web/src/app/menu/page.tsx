import type { Metadata } from "next";
import { MenuBrowser } from "@/components/menu/MenuBrowser";
import { Container } from "@/components/ui";

export const metadata: Metadata = {
  title: "Menu: BBQ boxes, family boxes and deals",
  description:
    "Chicken, beef and mutton ready-to-grill BBQ boxes from Rs 3,000. Every box serves 2 with grill, coal, pita and sides. Delivered across Lahore.",
  alternates: { canonical: "/menu" },
};

export default function MenuPage() {
  return (
    <>
      <Container className="pt-14 md:pt-20">
        <h1 className="text-5xl font-semibold tracking-[-0.035em] text-ash-50 md:text-6xl">Menu</h1>
        <p className="mt-4 max-w-[52ch] text-lg text-ash-300">
          Every box serves 2: about 850 g marinated meat, grill and coal, tong, 4 pita, hummus, chutney and salad.
        </p>
      </Container>
      <MenuBrowser />
    </>
  );
}
