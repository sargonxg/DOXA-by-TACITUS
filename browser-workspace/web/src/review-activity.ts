export type ReviewActivity = {
  method: 'doxa-browser-activity-v1';
  visibleMs: number;
  openedFindingCards: string[];
  scope: 'page_visible_time_including_waits';
  humanTimeSaved: null;
};

export function trackReviewActivity() {
  let accumulated = 0;
  let visibleSince: number | undefined;
  const opened = new Set<string>();
  let enabled = false;
  function pause() {
    if (visibleSince !== undefined) accumulated += Math.max(0, performance.now() - visibleSince);
    visibleSince = undefined;
  }
  document.addEventListener('visibilitychange', () => {
    pause();
    if (enabled && document.visibilityState === 'visible') visibleSince = performance.now();
  });
  return {
    start(previous?: unknown) {
      pause();
      accumulated = 0;
      opened.clear();
      if (previous && typeof previous === 'object') {
        const saved = previous as Partial<ReviewActivity>;
        if (typeof saved.visibleMs === 'number' && Number.isFinite(saved.visibleMs) && saved.visibleMs >= 0) accumulated = saved.visibleMs;
        if (Array.isArray(saved.openedFindingCards)) for (const key of saved.openedFindingCards) if (typeof key === 'string') opened.add(key);
      }
      enabled = true;
      visibleSince = document.visibilityState === 'visible' ? performance.now() : undefined;
    },
    open(key: string) { if (enabled) opened.add(key); },
    snapshot(): ReviewActivity {
      return { method: 'doxa-browser-activity-v1', visibleMs: Math.round(accumulated + (visibleSince === undefined ? 0 : Math.max(0, performance.now() - visibleSince))),
        openedFindingCards: [...opened], scope: 'page_visible_time_including_waits', humanTimeSaved: null };
    },
  };
}
