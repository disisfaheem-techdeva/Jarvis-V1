import Link from "next/link";
import type { Metadata } from "next";
import { MarketingPage } from "@/components/MarketingPage";
import { Bullets, LEGAL_UPDATED, Section } from "@/components/Prose";

export const metadata: Metadata = {
  title: "Privacy Policy — TechDeva",
  description: "What TechDeva collects, why, who processes it, and how to have it deleted.",
};

export default function PrivacyPage() {
  return (
    <MarketingPage
      eyebrow="Legal"
      title="Privacy Policy"
      body={`Last updated ${LEGAL_UPDATED}. This explains what TechDeva collects when a business uses our dashboard, WhatsApp agent and social posting, why, and how to have it deleted.`}
      cta={false}
    >
      <Section title="Who we are">
        <p>
          TechDeva provides software for service businesses (clinics, salons, academies): a business dashboard,
          an AI WhatsApp agent, and tools to publish the business&apos;s own posts to Facebook and Instagram.
          The business that uses TechDeva decides what its customers&apos; data is used for; TechDeva processes it
          on that business&apos;s behalf.
        </p>
      </Section>

      <Section title="What we collect">
        <Bullets
          items={[
            <>
              <b className="text-white">Account details</b>: the name, email and phone number of the business owner and
              staff who log in. Passwords are stored only as a one-way hash.
            </>,
            <>
              <b className="text-white">Business records the business enters</b>: customers, appointments, bills and
              payments, services and products, stock, expenses, staff and attendance.
            </>,
            <>
              <b className="text-white">WhatsApp conversations</b>: messages sent to a business&apos;s WhatsApp number that
              its TechDeva agent answers, and the agent&apos;s replies, so the business can see and continue them.
            </>,
            <>
              <b className="text-white">Facebook and Instagram connection</b>: when a business connects its Facebook
              account, we receive the list of Pages it manages, access tokens for those Pages, the linked Instagram
              business account (ID and username) and its ad account IDs. We do not receive or store the person&apos;s
              Facebook password.
            </>,
            <>
              <b className="text-white">Pictures uploaded for posts</b>: stored so Facebook and Instagram can fetch them
              when the post is published.
            </>,
            <>
              <b className="text-white">Website forms</b>: the name, business, phone, email and message you send us from
              the demo or data-deletion forms.
            </>,
          ]}
        />
      </Section>

      <Section title="How we use Facebook and Instagram data">
        <Bullets
          items={[
            "To publish to a business's own Facebook Page and Instagram account the posts that the business owner has approved, either immediately or at the time they chose. Nothing is posted without that approval.",
            "To show the business the Pages and Instagram account it connected, and the result of each post.",
            "To read the business's own ad account performance (spend, leads) and show it in its dashboard.",
          ]}
        />
        <p>
          We do not use Facebook or Instagram data for advertising to anyone else, we do not sell it, and we do not
          combine one business&apos;s data with another&apos;s. A business can disconnect Facebook at any time; the stored
          tokens are then no longer used, and are deleted on request (see below).
        </p>
      </Section>

      <Section title="How we use Google data">
        <Bullets
          items={[
            "When a business owner connects Google Ads, TechDeva reads that business's own campaign spend, impressions, clicks and conversions, read-only, to show them in its dashboard.",
            "We do not create, change or pause campaigns, we do not sell this data, and we do not use it for advertising or to train AI models.",
            "The data is shown only to that business's owner. A business can disconnect Google at any time; its stored Google sign-in is then deleted.",
          ]}
        />
        <p>
          TechDeva&apos;s use and transfer of information received from Google APIs adheres to the{" "}
          <a
            href="https://developers.google.com/terms/api-services-user-data-policy"
            className="text-white underline"
            target="_blank"
            rel="noopener noreferrer"
          >
            Google API Services User Data Policy
          </a>
          , including the Limited Use requirements.
        </p>
      </Section>

      <Section title="Who else processes data">
        <Bullets
          items={[
            "Google (Gemini AI): the text of a conversation or request is sent to Google's AI service to write the agent's reply, a post draft, or Jarvis's spoken answer. Data we receive from Facebook, Instagram or Google Ads is never sent to the AI service.",
            "Meta (Facebook, Instagram, WhatsApp): posts are published through Meta's API, and WhatsApp messages travel through WhatsApp.",
            "Our hosting provider, which runs the servers the platform and its database are on.",
          ]}
        />
        <p>We do not sell personal data to anyone.</p>
      </Section>

      <Section title="How we protect it">
        <Bullets
          items={[
            "Each business's data is kept apart at the database level: one business can never read another's records.",
            "Facebook and Instagram access tokens are stored encrypted.",
            "Access to the dashboard needs a login; staff accounts cannot see the owner-only pages (money, billing, posting).",
            "Every important change is recorded in an audit log.",
          ]}
        />
      </Section>

      <Section title="How long we keep it">
        <p>
          We keep a business&apos;s data while its account is open, so the business can use its history. When a business
          closes its account or asks us to, we delete its data within 30 days, except records we are required by law
          to keep. Database backups that contain deleted data expire on their normal schedule.
        </p>
      </Section>

      <Section title="Your choices and deleting your data">
        <p>
          A business owner can edit or remove most records from the dashboard, and can disconnect Facebook, Google and
          WhatsApp at any time. To have your data deleted, including data we received from Facebook or Google, use the form on
          our <Link href="/data-deletion" className="text-white underline">data deletion page</Link>. If you are a
          customer of a business that uses TechDeva, you can also ask that business directly; we will act on its
          instructions.
        </p>
      </Section>

      <Section title="Children">
        <p>TechDeva is a tool for businesses and is not directed at children.</p>
      </Section>

      <Section title="Changes and contact">
        <p>
          If we change this policy we will update the date at the top of this page. For any privacy question, send us
          a message through the <Link href="/demo" className="text-white underline">contact form</Link> and write
          &ldquo;Privacy&rdquo; in the message.
        </p>
      </Section>
    </MarketingPage>
  );
}
