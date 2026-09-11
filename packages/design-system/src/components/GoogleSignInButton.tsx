import type { JSX } from 'react';
import { cn } from '../lib/cn.js';

/** The three wordings Google's guidelines permit. Nothing else. */
export type GoogleSignInLabel =
  | 'Sign in with Google'
  | 'Sign up with Google'
  | 'Continue with Google';

export interface GoogleSignInButtonProps {
  /** Where the button sends the browser — the API's `/auth/google/start`. */
  href: string;
  label?: GoogleSignInLabel;
  fullWidth?: boolean;
  className?: string;
}

/**
 * "Sign in with Google" — Epic 20.
 *
 * THE ONE ELEMENT HERE THAT IS NOT DRAWN FROM THIS SYSTEM'S TOKENS, ON PURPOSE.
 * Google publishes branding requirements for this button — the mark, the
 * wording, the colours, the border, the padding — and following them is a
 * third-party brand-compliance requirement, the same category as a
 * "Powered by Stripe" mark. Google is a platform being integrated with,
 * not a competitor being imitated, so the no-competitor-reference rules do
 * not apply; the licensed-assets rule does, and the "G" is used exactly as
 * Google requires it to be used: standard colours, unaltered, on white.
 *
 * Read from developers.google.com/identity/branding-guidelines on
 * 2026-09-10, not recalled. What is followed: the three permitted wordings;
 * 14px/20px medium type; the light theme (white, `#747775` 1px inside
 * border, `#1F1F1F` text) and the dark theme (`#131314`, `#8E918F`,
 * `#E3E3E3`), chosen by this product's theme attribute; 12px before the
 * mark, 10px after it, 12px after the text; a 40px row. One deliberate
 * deviation, recorded: the guideline's face is Google Sans, which is not
 * openly licensed, so the button sets the wording in this product's UI face
 * at the guideline's size and weight.
 *
 * An anchor, not a button: pressing it is a top-level navigation to the API,
 * which redirects to Google. A `fetch` cannot follow a cross-site redirect
 * into a sign-in page, and should not try.
 */
export function GoogleSignInButton({
  href,
  label = 'Sign in with Google',
  fullWidth = false,
  className,
}: GoogleSignInButtonProps): JSX.Element {
  return (
    <a
      className={cn('avp-google-btn', fullWidth && 'avp-google-btn--full', className)}
      href={href}
    >
      <span className="avp-google-btn__mark" aria-hidden="true">
        {/* Google's standard-colour "G", from their button assets, unaltered. */}
        <svg viewBox="0 0 48 48" width="20" height="20" focusable="false">
          <path
            fill="#EA4335"
            d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
          />
          <path
            fill="#4285F4"
            d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
          />
          <path
            fill="#FBBC05"
            d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
          />
          <path
            fill="#34A853"
            d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
          />
        </svg>
      </span>
      <span className="avp-google-btn__label">{label}</span>
    </a>
  );
}
