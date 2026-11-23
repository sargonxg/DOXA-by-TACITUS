/** Product companion. Explanations, findings and review status stay in real text. */
export function DoxaCompanion({ className = "" }: { className?: string }) {
  return (
    <img
      className={`doxa-companion ${className}`.trim()}
      src="/brand/doxa-analyst.v1.webp"
      width={1230}
      height={1278}
      alt=""
      aria-hidden="true"
      decoding="async"
    />
  );
}
