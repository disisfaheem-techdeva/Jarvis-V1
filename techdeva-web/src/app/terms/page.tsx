import Link from "next/link";
import type { Metadata } from "next";
import { MarketingPage } from "@/components/MarketingPage";
import { Bullets, LEGAL_UPDATED, Section } from "@/components/Prose";
import { TRIAL_DAYS } from "@/lib/links";

export const metadata: Metadata = {
  title: "Terms of Service — TechDeva",
  description: "The terms for using the TechDeva business dashboard, WhatsApp agent and social posting.",
};

export default function TermsPage() {
  return (
    <MarketingPage
      eyebrow="Legal"
      title="Terms of Service"
      body={`Last updated ${LEGAL_UPDATED}. These terms apply to every business that uses TechDeva.`}
      cta={false}
    >
      <Section title="The service">
        <p>
          TechDeva gives your business a dashboard for appointments, customers, bills, stock, staff and money; an AI
          WhatsApp agent that answers your customers; and tools to publish your own posts to Facebook and Instagram.
        </p>
      </Section>

      <Section title="Free trial and payment">
        <p>
          A new business can use TechDeva free for {TRIAL_DAYS} days. After that, the monthly fee agreed with TechDeva
          applies. If the trial ends without a payment, your dashboard is locked until you pay; your data is kept and
          your WhatsApp agent keeps answering your customers. TechDeva sends bills through your dashboard (Plan &amp;
          Billing), and a bill counts as paid once TechDeva has received the money.
        </p>
      </Section>

      <Section title="Your responsibilities">
        <Bullets
          items={[
            "Keep your login private and give staff their own accounts.",
            "Only enter customer information you are allowed to hold, and tell your customers that you use an AI assistant on WhatsApp.",
            "Review what you publish: posts go out under your business name once you approve them.",
            "Follow WhatsApp's, Facebook's and Instagram's own rules when you use them through TechDeva.",
          ]}
        />
      </Section>

      <Section title="The AI agent">
        <p>
          The WhatsApp agent and Jarvis are AI and can make mistakes. They follow the information and rules your
          business gives them; check anything important (prices, medical advice, bookings) before relying on it.
          Conversations the agent cannot handle are passed to you.
        </p>
      </Section>

      <Section title="Your data">
        <p>
          Your business&apos;s data belongs to your business. We process it only to provide the service, as described in
          our <Link href="/privacy" className="text-white underline">Privacy Policy</Link>, and delete it on request
          (<Link href="/data-deletion" className="text-white underline">data deletion</Link>).
        </p>
      </Section>

      <Section title="Stopping">
        <p>
          You can stop using TechDeva at any time. We may suspend an account that is unpaid or used to break the law
          or the rules of WhatsApp or Meta, after telling you why.
        </p>
      </Section>

      <Section title="Liability">
        <p>
          We work to keep TechDeva available and correct, but we cannot promise it will never be interrupted. To the
          extent the law allows, TechDeva is not liable for indirect losses, such as lost profit, arising from use of
          the service.
        </p>
      </Section>

      <Section title="Questions">
        <p>
          Send us a message through the <Link href="/demo" className="text-white underline">contact form</Link>.
        </p>
      </Section>
    </MarketingPage>
  );
}
