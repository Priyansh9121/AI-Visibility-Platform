/**
 * The front door's two forms, addressable from other pages — 2026-09-29.
 *
 * Sign-in and sign-up are VIEW STATES of `/`, not routes (`app/page.tsx`
 * explains why), which was fine while the only way to reach them was `/`'s
 * own header. The public privacy and terms pages carry that same header, and
 * a "Log in" there that landed on the marketing page — to be pressed a second
 * time — would be a button that does not do what it says. So `/?view=sign-in`
 * and `/?view=sign-up` open the form straight away for someone signed out;
 * someone signed in goes to intake exactly as before. The word is taken off
 * the address bar on arrival, the same way the Google failure reason is.
 */

export type FrontDoorForm = 'sign-in' | 'sign-up';

/** Where a page other than `/` sends "Log in" and "Get started". */
export function frontDoorHref(form: FrontDoorForm): string {
  return `/?view=${form}`;
}

/** Read `?view=` off the address bar and clear the query. Null for anything else. */
export function takeFrontDoorForm(
  win: { location: Pick<Location, 'search' | 'pathname'>; history: Pick<History, 'replaceState'> },
): FrontDoorForm | null {
  const view = new URLSearchParams(win.location.search).get('view');
  if (view !== 'sign-in' && view !== 'sign-up') return null;
  win.history.replaceState(null, '', win.location.pathname);
  return view;
}
