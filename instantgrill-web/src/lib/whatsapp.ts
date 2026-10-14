import { brand } from "./catalog";

/** wa.me link to the shop's WhatsApp with a prefilled message. */
export function whatsappLink(text: string) {
  return `https://wa.me/${brand.whatsapp}?text=${encodeURIComponent(text)}`;
}
