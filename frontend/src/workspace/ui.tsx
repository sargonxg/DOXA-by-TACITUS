import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CircleHelp,
  FileSearch,
  LoaderCircle,
  X,
} from "lucide-react";
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
      <FileSearch size={26} strokeWidth={1.3} />
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
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog
      className={`w-drawer ${wide ? "wide" : ""}`}
      ref={ref}
      onCancel={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      <header>
        <div>
          {eyebrow && <span className="w-eyebrow">{eyebrow}</span>}
          <h2>{title}</h2>
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
  }, [items]);
  const start = Math.max(0, Math.floor(top / rowHeight) - 4);
  const end = Math.min(items.length, Math.ceil((top + height) / rowHeight) + 4);
  return (
    <div
      className="w-virtual"
      ref={ref}
      onScroll={(event) => setTop(event.currentTarget.scrollTop)}
      aria-label={label}
      role="list"
    >
      <div style={{ height: items.length * rowHeight, position: "relative" }}>
        {items.slice(start, end).map((item, offset) => (
          <div
            role="listitem"
            key={start + offset}
            style={{
              position: "absolute",
              top: (start + offset) * rowHeight,
              height: rowHeight,
              width: "100%",
            }}
          >
            {render(item, start + offset)}
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
