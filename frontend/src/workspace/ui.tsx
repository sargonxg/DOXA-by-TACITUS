import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CircleHelp,
  LoaderCircle,
  X,
} from "lucide-react";
import { DoxaCompanion } from "./DoxaCompanion";
export const number = (value: unknown) =>
  new Intl.NumberFormat("en", { maximumFractionDigits: 0 }).format(
    Number(value) || 0,
  );
export const human = (value?: unknown) =>
  String(value || "Unspecified")
    .replaceAll("_", " ")
    .replaceAll("-", " ");
export const date = (value?: string) =>
  value
    ? new Date(value).toLocaleDateString("en", {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      })
    : "Undated";
export const percentage = (value?: number) =>
  `${Math.max(0, Math.min(100, value || 0))
    .toFixed(1)
    .replace(".0", "")}%`;
export function cueTime(milliseconds: number): string {
  const whole = Math.floor(milliseconds / 1000);
  return [Math.floor(whole / 3600), Math.floor(whole / 60) % 60, whole % 60]
    .map((part) => String(part).padStart(2, "0")).join(":") +
    "." + String(milliseconds % 1000).padStart(3, "0");
}
export function Button({
  children,
  onClick,
  disabled,
  variant = "",
  type = "button",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: string;
  type?: "button" | "submit";
  title?: string;
}) {
  return (
    <button
      className={`w-button ${variant}`}
      onClick={onClick}
      disabled={disabled}
      type={type}
      title={title}
    >
      {children}
    </button>
  );
}
export function IconButton({
  children,
  label,
  onClick,
  disabled,
}: {
  children: ReactNode;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      className="w-icon"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
export function Badge({
  children,
  tone = "",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`w-badge ${tone}`}>{children}</span>;
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="w-empty">
      <DoxaCompanion className="doxa-companion--empty" />
      <strong>{title}</strong>
      <div className="w-empty-copy">{children}</div>
    </div>
  );
}
export function Spinner({ text = "Loading…" }: { text?: string }) {
  return (
    <div className="w-spinner">
      <LoaderCircle size={20} className="spin" />
      <span>{text}</span>
    </div>
  );
}
export function ErrorMessage({ text }: { text: string }) {
  return (
    <div className="w-error" role="alert">
      <CircleHelp size={17} />
      <span>{text}</span>
    </div>
  );
}
export function Drawer({
  title,
  eyebrow,
  children,
  onClose,
  wide = false,
  notification,
}: {
  title: string;
  eyebrow?: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
  notification?: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const opener =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    ref.current?.showModal();
    return () => {
      ref.current?.close();
      if (opener?.isConnected)
        requestAnimationFrame(() => opener.focus({ preventScroll: true }));
    };
  }, []);
  return (
    <dialog
      className={`w-drawer ${wide ? "wide" : ""}`}
      ref={ref}
      aria-labelledby={titleId}
      onCancel={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      <header>
        <div>
          {eyebrow && <span className="w-eyebrow">{eyebrow}</span>}
          <h2 id={titleId}>{title}</h2>
        </div>
        <IconButton label="Close panel" onClick={onClose}>
          <X size={19} />
        </IconButton>
      </header>
      {notification}
      <div className="w-drawer-body">{children}</div>
    </dialog>
  );
}
export function VirtualList<T>({
  items,
  rowHeight,
  render,
  label,
}: {
  items: T[];
  rowHeight: number;
  render: (item: T, index: number) => ReactNode;
  label: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(500);
  const [top, setTop] = useState(0);
  const [focused, setFocused] = useState<number | null>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const observer = new ResizeObserver((entries) =>
      setHeight(entries[0]?.contentRect.height || 500),
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = 0;
    setTop(0);
    setFocused(null);
  }, [items]);
  const start = Math.max(0, Math.floor(top / rowHeight) - 4);
  const end = Math.min(items.length, Math.ceil((top + height) / rowHeight) + 4);
  const visible = [
    ...new Set([
      ...Array.from(
        { length: Math.max(0, end - start) },
        (_, index) => start + index,
      ),
      ...(focused !== null && focused < items.length ? [focused] : []),
    ]),
  ].sort((a, b) => a - b);
  return (
    <div
      className="w-virtual"
      ref={ref}
      onScroll={(event) => setTop(event.currentTarget.scrollTop)}
      aria-label={label}
      role="list"
      aria-description="Use Arrow Up and Arrow Down to move within this loaded page; Home and End select its first and last row. Result-page controls load the next bounded page."
      tabIndex={0}
      onFocusCapture={(event) => {
        const row = (event.target as HTMLElement).closest<HTMLElement>(
          "[data-virtual-index]",
        );
        if (row) setFocused(Number(row.dataset.virtualIndex));
      }}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null))
          setFocused(null);
      }}
      onKeyDown={(event) => {
        if (
          !["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key) ||
          (event.target as HTMLElement).matches("input,textarea,select")
        )
          return;
        const current = Number(
          (event.target as HTMLElement).closest<HTMLElement>(
            "[data-virtual-index]",
          )?.dataset.virtualIndex ?? -1,
        );
        const next =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? items.length - 1
              : Math.max(
                  0,
                  Math.min(
                    items.length - 1,
                    current + (event.key === "ArrowDown" ? 1 : -1),
                  ),
                );
        if (next < 0 || !items.length) return;
        event.preventDefault();
        setFocused(next);
        const node = ref.current;
        if (!node) return;
        if (
          next * rowHeight < node.scrollTop ||
          (next + 1) * rowHeight > node.scrollTop + node.clientHeight
        ) {
          node.scrollTop = next * rowHeight;
          setTop(node.scrollTop);
        }
        requestAnimationFrame(() =>
          ref.current
            ?.querySelector<HTMLElement>(
              `[data-virtual-index="${next}"] button,[data-virtual-index="${next}"] a,[data-virtual-index="${next}"] [tabindex]`,
            )
            ?.focus({ preventScroll: true }),
        );
      }}
    >
      <div style={{ height: items.length * rowHeight, position: "relative" }}>
        {visible.map((index) => (
          <div
            role="listitem"
            key={index}
            data-virtual-index={index}
            aria-posinset={index + 1}
            aria-setsize={items.length}
            style={{
              position: "absolute",
              top: index * rowHeight,
              height: rowHeight,
              width: "100%",
            }}
          >
            {render(items[index], index)}
          </div>
        ))}
      </div>
    </div>
  );
}
export function Pager({
  total,
  page,
  next,
  loading,
  onPrevious,
  onNext,
}: {
  total: number;
  page: number;
  next?: string | null;
  loading?: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  return (
    <div className="w-pager">
      <span>
        {number(total)} matching · page {page + 1}
      </span>
      <IconButton
        label="Previous result page"
        onClick={onPrevious}
        disabled={page === 0 || loading}
      >
        <ArrowLeft size={14} />
      </IconButton>
      <IconButton
        label="Next result page"
        onClick={onNext}
        disabled={!next || loading}
      >
        <ArrowRight size={14} />
      </IconButton>
    </div>
  );
}
