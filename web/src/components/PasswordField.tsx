"use client";

import { useId, useRef, useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { clsx } from "@/lib/clsx";

/**
 * A password input with a show/hide ("ματάκι") toggle.
 *
 * One component rather than the same JSX pasted into each form, so the three
 * things that are easy to get wrong are got right once:
 *
 *   * `type="button"` on the toggle. Inside a <form>, a button with no type
 *     defaults to `submit` — clicking the eye would submit the login form with
 *     a half-typed password. This is the whole reason not to inline it.
 *   * The caret survives the toggle. Switching an input's `type` moves the
 *     caret to the end in several browsers, which is maddening mid-password, so
 *     the selection is captured and restored.
 *   * It is announced, not just drawn: `aria-pressed` carries the state and the
 *     label says which way the click goes, so a screen-reader user is not told
 *     only "button".
 *
 * Starts HIDDEN always. Revealing is a deliberate act by the person at the
 * keyboard; nothing here remembers the choice across fields or page loads,
 * because a password left on screen is the failure mode this feature invites.
 */
export function PasswordField({
  id,
  value,
  onChange,
  autoComplete,
  label,
  hint,
  className,
  labelClassName = "mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400",
  hintClassName = "mt-1 text-[11px] text-slate-400 dark:text-slate-500",
  autoFocus,
  required,
  name,
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  /** "current-password" on login, "new-password" on register/reset. */
  autoComplete: string;
  label: string;
  hint?: React.ReactNode;
  /** The form's input styling, passed in so each page keeps its own look. */
  className?: string;
  /** …and the label/hint styling with it. The auth forms sit on a fixed blue
   *  background where the slate defaults below are barely legible, and the
   *  theme-aware variants do not help — that background is dark in light mode
   *  too. Defaulted, so every in-app caller is unchanged. */
  labelClassName?: string;
  hintClassName?: string;
  autoFocus?: boolean;
  required?: boolean;
  name?: string;
}) {
  const [visible, setVisible] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const generatedId = useId();
  const inputId = id ?? generatedId;

  function toggle() {
    const input = inputRef.current;
    // Read the caret BEFORE the type flips — after the re-render the browser
    // may have already moved it.
    const start = input?.selectionStart ?? null;
    const end = input?.selectionEnd ?? null;
    setVisible((v) => !v);
    // After paint: the input has to exist with its new type before the
    // selection can be put back.
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      if (start !== null && end !== null) {
        try {
          el.setSelectionRange(start, end);
        } catch {
          /* setSelectionRange throws on some input types — not worth failing a
             login over. */
        }
      }
    });
  }

  return (
    <div>
      <label className={labelClassName} htmlFor={inputId}>
        {label}
      </label>
      <div className="relative">
        <input
          id={inputId}
          name={name}
          ref={inputRef}
          type={visible ? "text" : "password"}
          className={clsx(className, "pr-10")}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete={autoComplete}
          autoFocus={autoFocus}
          required={required}
          // Keeps a phone keyboard from "helpfully" capitalising or correcting
          // a password the moment it becomes visible text.
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
        />
        <button
          // Not "submit" — see the component docstring.
          type="button"
          onClick={toggle}
          // Both the accessible name and the tooltip say what the NEXT click
          // does, which is what someone reaching for it wants to know.
          aria-label={visible ? "Απόκρυψη κωδικού" : "Εμφάνιση κωδικού"}
          title={visible ? "Απόκρυψη κωδικού" : "Εμφάνιση κωδικού"}
          aria-pressed={visible}
          aria-controls={inputId}
          // Inherits the surrounding text colour instead of naming a slate:
          // this field is used on white cards and on the auth page's dark glass
          // one, and a fixed grey is invisible on one of the two.
          className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-lg text-current opacity-50 transition hover:opacity-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500"
        >
          {visible ? (
            <EyeOff className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Eye className="h-4 w-4" aria-hidden="true" />
          )}
        </button>
      </div>
      {hint ? <p className={hintClassName}>{hint}</p> : null}
    </div>
  );
}
