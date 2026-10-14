import { mapEmbedUrl, mapLinkUrl } from "@/lib/catalog";

export function MapEmbed({ className = "" }: { className?: string }) {
  return (
    <div className={`overflow-hidden rounded-[var(--radius-card)] ring-1 ring-white/[0.06] ${className}`}>
      <iframe
        src={mapEmbedUrl}
        title="Map: Instant Grill kitchen at Orange Square, Baghbanpura, Lahore"
        loading="lazy"
        referrerPolicy="no-referrer-when-downgrade"
        className="h-80 w-full border-0 grayscale-[0.4] invert-[0.92] hue-rotate-180"
      />
      <a href={mapLinkUrl} target="_blank" rel="noopener noreferrer" className="block bg-coal-900 px-5 py-3 text-sm text-ember-300 hover:underline">
        Open in Google Maps
      </a>
    </div>
  );
}
