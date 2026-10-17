import type { Metadata } from "next";
import { Clock, EnvelopeSimple, FacebookLogo, InstagramLogo, MapPin, Phone, TiktokLogo, WhatsappLogo } from "@phosphor-icons/react/dist/ssr";
import { ContactForm } from "@/components/forms/ContactForm";
import { MapEmbed } from "@/components/MapEmbed";
import { PageHeader } from "@/components/PageHeader";
import { Container, buttonClass } from "@/components/ui";
import { whatsappLink } from "@/lib/whatsapp";
import { brand } from "@/lib/catalog";
import { ldJson, restaurantJsonLd } from "@/lib/jsonld";

export const metadata: Metadata = {
  title: "Contact us",
  description: `Order or ask on WhatsApp ${brand.phoneDisplay}, email ${brand.email}, or visit our kitchen at Orange Square, Baghbanpura, Lahore. Open daily 12 PM to 12 AM.`,
  alternates: { canonical: "/contact" },
};

export default function ContactPage() {
  const rows = [
    { icon: Phone, label: brand.phoneDisplay, href: `tel:+${brand.whatsapp}` },
    { icon: EnvelopeSimple, label: brand.email, href: `mailto:${brand.email}` },
    { icon: MapPin, label: brand.address },
    { icon: Clock, label: brand.hoursDisplay },
  ];
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={ldJson(restaurantJsonLd())} />
      <PageHeader title="Talk to us" intro="The fastest way is WhatsApp. A person answers during opening hours." />
      <Container className="grid gap-10 py-14 lg:grid-cols-[1fr_1.2fr]">
        <div className="space-y-8">
          <a href={whatsappLink("Assalam o Alaikum Instant Grill")} target="_blank" rel="noopener noreferrer" className={buttonClass("primary", "lg")}>
            <WhatsappLogo size={20} weight="fill" /> WhatsApp {brand.phoneDisplay}
          </a>
          <ul className="space-y-4">
            {rows.map(({ icon: Icon, label, href }) => (
              <li key={label} className="flex items-start gap-3 text-ash-100">
                <Icon size={22} className="mt-0.5 shrink-0 text-ember-400" />
                {href ? (
                  <a href={href} className="hover:text-ember-300">
                    {label}
                  </a>
                ) : (
                  <span>{label}</span>
                )}
              </li>
            ))}
          </ul>
          <div className="flex gap-2">
            {[
              { href: brand.instagram, label: "Instagram @instant_grill.shop", Icon: InstagramLogo },
              { href: brand.tiktok, label: "TikTok @instantgrill.shop", Icon: TiktokLogo },
              { href: brand.facebook, label: "Facebook: Instant Grill", Icon: FacebookLogo },
            ].map(({ href, label, Icon }) => (
              <a key={label} href={href} target="_blank" rel="noopener noreferrer" aria-label={label} className="grid size-11 place-items-center rounded-full border border-coal-600 text-ash-300 hover:border-ash-400 hover:text-ash-50">
                <Icon size={20} />
              </a>
            ))}
          </div>
          <MapEmbed />
        </div>
        <div>
          <h2 className="mb-5 text-2xl font-semibold text-ash-50">Send a message</h2>
          <ContactForm />
        </div>
      </Container>
    </>
  );
}
