// Small design-system primitives shared by every view.
import { useEffect, useState, type ButtonHTMLAttributes, type ReactNode } from 'react';

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(' ');
}

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
export function Button({ variant = 'secondary', size = 'md', className, children, ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: 'sm' | 'md' }) {
  const base = 'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:opacity-40 disabled:pointer-events-none whitespace-nowrap';
  const sizes = { sm: 'h-7 px-2.5 text-[12.5px]', md: 'h-8.5 px-3.5 text-[13px]' };
  const variants: Record<Variant, string> = {
    primary: 'bg-accent text-ink-950 hover:bg-[#82e0fa] shadow-[0_0_14px_rgba(79,209,245,0.25)]',
    secondary: 'bg-ink-700 text-fg hover:bg-ink-600 border border-ink-600/60',
    ghost: 'text-muted hover:text-fg hover:bg-ink-700',
    danger: 'bg-danger/15 text-danger hover:bg-danger/25 border border-danger/25',
  };
  return <button className={cx(base, sizes[size], variants[variant], className)} {...rest}>{children}</button>;
}

export function IconButton({ label, className, children, active, ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { label: string; active?: boolean }) {
  return (
    <button aria-label={label} title={label}
      className={cx('grid place-items-center size-8 rounded-lg transition-colors', active ? 'bg-ink-700 text-fg' : 'text-muted hover:text-fg hover:bg-ink-700', className)}
      {...rest}>
      {children}
    </button>
  );
}

export function Card({ title, description, actions, children, className }:
  { title?: ReactNode; description?: ReactNode; actions?: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <section className={cx('rounded-xl border border-ink-700 bg-ink-850', className)}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-4 px-5 pt-4 pb-3">
          <div>
            {title && <h3 className="text-[14px] font-semibold text-fg">{title}</h3>}
            {description && <p className="mt-0.5 text-[12.5px] leading-relaxed text-muted">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cx('px-5 pb-4', !(title || actions) && 'pt-4')}>{children}</div>
    </section>
  );
}

export function Row({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-6 border-t border-ink-700/70 py-3 first:border-t-0">
      <div className="min-w-0">
        <div className="text-[13px] text-fg">{label}</div>
        {hint && <div className="mt-0.5 text-[12px] leading-relaxed text-muted">{hint}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

export function Toggle({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string }) {
  return (
    <button role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)}
      className={cx('relative h-5 w-9 rounded-full transition-colors disabled:opacity-40', checked ? 'bg-accent-strong' : 'bg-ink-600')}>
      <span className={cx('absolute top-0.5 size-4 rounded-full bg-white shadow transition-[left]', checked ? 'left-[18px]' : 'left-0.5')} />
    </button>
  );
}

export function Select<T extends string>({ value, options, onChange, className, disabled }:
  { value: T; options: { value: T; label: string; disabled?: boolean }[]; onChange: (v: T) => void; className?: string; disabled?: boolean }) {
  return (
    <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as T)}
      className={cx('h-8 rounded-lg border border-ink-600 bg-ink-800 px-2.5 text-[13px] outline-none hover:border-ink-500 focus:border-accent/60 disabled:opacity-50', className)}>
      {options.map((o) => <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>)}
    </select>
  );
}

/** Text/number input that commits on blur or Enter (avoids saving on every keystroke). */
export function CommitInput({ value, onCommit, type = 'text', className, placeholder, min, max, step, mono }:
  { value: string | number; onCommit: (v: string) => void; type?: string; className?: string; placeholder?: string; min?: number; max?: number; step?: number; mono?: boolean }) {
  const [draft, setDraft] = useState(String(value ?? ''));
  useEffect(() => setDraft(String(value ?? '')), [value]);
  const commit = () => { if (draft !== String(value ?? '')) onCommit(draft); };
  return (
    <input type={type} value={draft} placeholder={placeholder} min={min} max={max} step={step}
      onChange={(e) => setDraft(e.target.value)} onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setDraft(String(value ?? '')); }}
      className={cx('h-8 rounded-lg border border-ink-600 bg-ink-800 px-2.5 text-[13px] outline-none placeholder:text-faint hover:border-ink-500 focus:border-accent/60', mono && 'font-mono text-[12.5px]', className)} />
  );
}

export function Slider({ value, min, max, step, onCommit, format }:
  { value: number; min: number; max: number; step: number; onCommit: (v: number) => void; format?: (v: number) => string }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <div className="flex items-center gap-3">
      <input type="range" min={min} max={max} step={step} value={v} onChange={(e) => setV(Number(e.target.value))}
        onPointerUp={() => v !== value && onCommit(v)} onKeyUp={() => v !== value && onCommit(v)}
        className="w-40 accent-[var(--color-accent)]" />
      <span className="w-14 text-right font-mono text-[12px] text-muted tabular-nums">{format ? format(v) : v}</span>
    </div>
  );
}

export function Badge({ tone = 'neutral', children, className }: { tone?: 'neutral' | 'good' | 'warn' | 'bad' | 'accent'; children: ReactNode; className?: string }) {
  const tones = {
    neutral: 'bg-ink-700 text-muted', good: 'bg-accent/12 text-accent', warn: 'bg-warm/12 text-warm',
    bad: 'bg-danger/12 text-danger', accent: 'bg-accent-dim text-accent',
  };
  return <span className={cx('inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium', tones[tone], className)}>{children}</span>;
}

export function Spinner({ className }: { className?: string }) {
  return <span className={cx('inline-block size-3.5 rounded-full border-2 border-current border-t-transparent animate-spin', className)} />;
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      {icon && <div className="text-faint">{icon}</div>}
      <div className="text-[13.5px] font-medium text-fg">{title}</div>
      {children && <div className="max-w-sm text-[12.5px] leading-relaxed text-muted">{children}</div>}
    </div>
  );
}

export function Meter({ value, tone = 'good' }: { value: number; tone?: 'good' | 'warn' | 'bad' }) {
  const color = { good: 'bg-accent', warn: 'bg-warm', bad: 'bg-danger' }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-700">
      <div className={cx('h-full rounded-full transition-[width]', color)} style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} />
    </div>
  );
}
