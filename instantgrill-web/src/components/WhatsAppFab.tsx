import { WhatsappLogo } from "@phosphor-icons/react/dist/ssr";
import { brand } from "@/lib/catalog";

export function whatsappLink(text: string) {
  return `https://wa.me/${brand.whatsapp}?text=${encodeURIComponent(text)}`;
}

export function WhatsAppFab() {
  return (
    <a
      href={whatsappLink("Assalam o Alaikum Instant Grill, I want to order a BBQ box.")}
      target="_blank"
      rel="noopener noreferrer"
      aria-label="Chat with us on WhatsApp"
      className="fixed bottom-24 right-4 z-30 grid size-14 place-items-center rounded-full bg-[#25d366] text-coal-950 shadow-[0_12px_30px_-8px_rgb(37_211_102/0.5)] transition-transform hover:scale-105 active:scale-95 md:bottom-6 md:right-6"
    >
      <WhatsappLogo size={28} weight="fill" />
    </a>
  );
}
