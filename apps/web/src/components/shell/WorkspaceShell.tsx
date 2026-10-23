'use client';

/**
 * The signed-in frame — Epic 9.13, and since Epic 13 a frame with TWO MODES.
 *
 * Wraps `AppShell` with this product's actual destinations so no screen has to
 * restate them. It frames what Epics 7 / 9.8 / 9.9 / 9.10 settled and changes
 * none of it.
 *
 * **Every item in the sidebar leads somewhere real.** There is no disabled nav
 * item and no item whose click does nothing: Settings leads to a screen that
 * states plainly what is not built yet, which is a destination rather than a
 * stub. The Clients row opens a list, and the clients screen is the first row
 * inside it.
 *
 * `/share` deliberately does NOT use this — a stranger reading a
 * report should not be shown the sending agency's workspace navigation.
 *
 * IT IS IN COLOUR NOW — Epic 9.24
 * -------------------------------
 * Each destination carries a fixed index into the Working-screen accent layer
 * (`BENCH_ACCENTS`). The indices are written out by name in `sidebars.tsx`
 * rather than derived from array position, because they are IDENTITY: Clients
 * has been violet since that epic and must still be violet after someone
 * reorders the sidebar or inserts a fifth destination between two existing
 * ones.
 *
 * TWO MODES — Epic 13
 * -------------------
 * Epic 9.20 kept this sidebar agency-wide and put a client's own navigation in
 * a strip inside the content area, on the argument that depth about one client
 * cannot live here "without inventing a global selected client the rest of
 * the product does not have". Epic 13 reverses that on the founder's decision;
 * the build log entry "Epic 13 — the sidebar becomes the map" records the
 * original argument and why it changed.
 *
 * What survives of the argument is the rule it was protecting: an item in
 * this sidebar is always about ONE thing. So the sidebar switches modes rather
 * than mixing levels. Given a `client`, it is that client's map — the head
 * names the client and the way back, the items are `CLIENT_NAV`. Given none,
 * it is the agency's, exactly as before plus the client list.
 *
 * THE SELECTED CLIENT IS THE URL, NOT A STORE
 * -------------------------------------------
 * `client` arrives as a prop from `ClientSpace`, which every screen under
 * `/clients/[clientId]/*` already renders with the record its route loaded.
 * Nothing here reads `useParams`, nothing is kept in context, and a screen
 * whose URL names no client has no selected client. That keeps both modes
 * renderable to static markup, which is how they are tested.
 *
 * This is chrome only. design-direction.md §0 puts every screen behind this
 * shell in the Working column; the report it links to is Presenting and is
 * untouched, which `reportIsolation.test.ts` enforces rather than promises.
 */

import { useCallback, useEffect, useRef, useState, type JSX, type ReactNode } from 'react';
import { AppShell, Button } from '@avp/design-system';
import { api, ApiProblem } from '@/lib/api';
import { latestScores, sidebarClients } from '@/lib/shell/clientList';
import {
  AgencyNav,
  ClientHead,
  ClientNav,
  type ClientListState,
  type ShellClient,
  type ShellSection,
} from '@/components/shell/sidebars';
import { FeedbackLink } from '@/components/shell/FeedbackLink';

export type { ShellSection, ShellClient };

/**
 * Whether the Clients list was left open — remembered for the session.
 *
 * The list is opened tens of times a day by an operator moving between
 * clients, and a disclosure that closed itself on every navigation would be a
 * click tax on the most-used path in the product. Session storage, not local:
 * a fresh visit starts closed, which is the calmer default for a first look.
 * Every access is guarded — a private window or a blocked store must never
 * cost the sidebar.
 */
const OPEN_KEY = 'avp.sidebar.clients';

function readOpen(): boolean {
  try {
    return window.sessionStorage.getItem(OPEN_KEY) === '1';
  } catch {
    return false;
  }
}

function writeOpen(open: boolean): void {
  try {
    window.sessionStorage.setItem(OPEN_KEY, open ? '1' : '0');
  } catch {
    // Nothing to do: the preference simply is not remembered.
  }
}

/**
 * What the footer says when Sign out did not work — copy fixes, 1e; the founder's own sentence.
 *
 * Until then a sign-out that failed said nothing at all: the handler had no failure path, so the
 * rejection went unhandled and the person was left on the page, not knowing whether the click had
 * done anything. Now a 401 counts as signed out (it is the API's answer when there is no session
 * to end: no cookie, a session that expired or was never there, an account since cut off), and
 * does what a sign-out that worked does. Anything else (a 503 while Redis is down, which leaves
 * the cookie alone; a 500; a refused origin; no answer at all) leaves the person signed in, on
 * the same page, with this sentence above the button and the button still there to press.
 */
const SIGN_OUT_FAILED_SENTENCE = 'Sign-out did not complete. Try again.';

export function WorkspaceShell({
  current,
  agencyName,
  seats,
  feedbackEmail,
  wide = false,
  client,
  children,
}: {
  current: ShellSection;
  /**
   * Omitted while the screen is still loading — the frame renders regardless,
   * so navigation is usable before the data arrives.
   *
   * Explicitly `| undefined` because this project runs
   * `exactOptionalPropertyTypes`, under which `?:` alone rejects a value that
   * is present-but-undefined — which is exactly what `me?.agency.name` is.
   */
  agencyName?: string | undefined;
  seats?: { used: number; limit: number } | undefined;
  /**
   * Where the footer's "Send feedback" link writes to — 2026-10-06: `me.feedbackEmail`, the API's
   * `FEEDBACK_EMAIL`, from the `/auth/me` the page already reads (the dashboard's is its standing
   * read's, `useScanStanding`) or, for the report, which reads none, `useFeedbackEmail`. Null,
   * undefined or anything that is not one address draws no link, and
   * then the footer is exactly what it was: that is the default, until the founder sets the
   * variable on the API service.
   */
  feedbackEmail?: string | null | undefined;
  wide?: boolean | undefined;
  /** Present on every screen inside one client's space; absent everywhere else. */
  client?: ShellClient | undefined;
  children: ReactNode;
}): JSX.Element {
  // Starts closed on the server and on first paint, then adopts the remembered
  // state — reading storage during render would disagree with the server
  // markup and React would refuse the hydration.
  const [clientsOpen, setClientsOpen] = useState(current === 'clients');
  const [list, setList] = useState<ClientListState>({ kind: 'idle' });
  const [signOutFailed, setSignOutFailed] = useState(false);
  const signOutGroup = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (current !== 'clients' && readOpen()) setClientsOpen(true);
  }, [current]);

  /*
   * SIGN OUT — copy fixes, 1e.
   *
   * The sentence is cleared when the button is pressed and put back if the sign-out fails again,
   * so a second failure is a NEW paragraph added to the region: the same words in the same element
   * would be no change to a live region, and a screen reader would say nothing the second time.
   * A success is a full navigation, as it always was; a 401 is treated as one (see
   * `SIGN_OUT_FAILED_SENTENCE`), and nothing else navigates.
   */
  async function signOut(): Promise<void> {
    setSignOutFailed(false);
    try {
      await api.logOut();
    } catch (err) {
      if (!(err instanceof ApiProblem && err.status === 401)) {
        setSignOutFailed(true);
        return;
      }
    }
    window.location.assign('/');
  }

  /*
   * THE SENTENCE AND THE BUTTON ARE BROUGHT INTO VIEW TOGETHER, because the footer is not always
   * in it. Under 60rem the sidebar is a strip that scrolls sideways, and on a phone the footer
   * starts inside the screen and runs past its edge (seen in a browser at 390 px, before any
   * sentence is added to it), so words added to it can be cut off. The wide sidebar scrolls on a
   * short window, and in a client's space it is long: the sentence grows the footer, and the
   * button to try again can be pushed below the fold while the sentence stays in sight. So it is
   * the group of the two that is scrolled to, not the paragraph. `nearest` moves nothing when the
   * group is already fully in view, which is the usual case; otherwise it scrolls the sidebar,
   * or the strip, just far enough.
   */
  useEffect(() => {
    if (signOutFailed) {
      signOutGroup.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
  }, [signOutFailed]);

  /*
   * THE LIST LOADS ONCE, AND THE FETCH OUTLIVES ITS OWN STATE CHANGE — Epic 14.2.
   *
   * As shipped in Epic 13 this effect depended on `[clientsOpen, list.kind]`
   * and set `list.kind` to `loading` inside its own body. That is a cycle:
   * the state change re-ran the effect, React first ran the previous run's
   * cleanup — which flipped `cancelled` — and the second run returned early
   * because the list was no longer `idle`. The response then arrived into a
   * closure that had been told to discard it, and "Loading clients…" stayed
   * on screen for every account, forever. A static render cannot show a
   * state that never arrives, which is why nothing caught it until the
   * founder opened the disclosure live; `WorkspaceShell.test.tsx` now mounts
   * the real component and clicks.
   *
   * So the fetch is keyed on the disclosure OPENING and nothing else. A ref
   * makes it fire once per mount, and the only thing that cancels it is the
   * component going away.
   */
  const fetchStarted = useRef(false);
  const unmounted = useRef(false);
  useEffect(
    () => () => {
      unmounted.current = true;
    },
    [],
  );

  useEffect(() => {
    if (!clientsOpen || fetchStarted.current) return;
    fetchStarted.current = true;
    setList({ kind: 'loading' });
    void (async () => {
      try {
        // Two reads in parallel, both of which already exist: the identities,
        // and the recent scans the scores are read off. See lib/shell/clientList
        // for what that derivation can and cannot claim.
        const [page, dashboard] = await Promise.all([api.clients(), api.dashboard(50)]);
        if (unmounted.current) return;
        setList({
          kind: 'ready',
          rows: sidebarClients(page.data, latestScores(dashboard)),
          more: page.nextCursor !== null,
        });
      } catch {
        if (!unmounted.current) setList({ kind: 'error' });
      }
    })();
  }, [clientsOpen]);

  const toggleClients = useCallback(() => {
    setClientsOpen((open) => {
      writeOpen(!open);
      return !open;
    });
  }, []);

  return (
    <AppShell
      wide={wide}
      brand={
        client ? (
          <ClientHead client={client} />
        ) : (
          <>
            <span className="text-ui-2xs font-semibold uppercase tracking-caps text-text-tertiary">
              Agency
            </span>
            <span className="font-display text-ed-2xs font-semibold leading-display tracking-display text-text-primary">
              {agencyName ?? '—'}
            </span>
          </>
        )
      }
      nav={
        client ? (
          <ClientNav client={client} />
        ) : (
          <AgencyNav
            current={current}
            clientsOpen={clientsOpen}
            onToggleClients={toggleClients}
            clients={list}
          />
        )
      }
      footer={
        <div className="flex flex-col gap-3">
          {client && agencyName != null && (
            <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">{agencyName}</p>
          )}
          {seats && (
            <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
              {`${seats.used} of ${seats.limit} seats`}
            </p>
          )}
          {feedbackEmail != null && <FeedbackLink address={feedbackEmail} />}
          {/*
            The alert region is on the page from the first render, empty, and the sentence is added
            to it, because a screen reader announces what a live region gains and mostly not a
            region that arrives with its text in it (the same lesson as `PasswordChangePanel`'s
            status, 3.85). It shares a wrapper with the button, and is not a flex item of the
            footer, which has a gap: an empty region of its own would have added one to a footer
            that has said nothing. The sentence is above the button, so that in the wide sidebar
            the footer grows upward from the bottom and the button stays where the pointer is. The
            button is `fullWidth`, as it was when it was a flex item of the footer and stretched:
            in the wrapper it would otherwise be as wide as its label.
          */}
          <div ref={signOutGroup}>
            <div role="alert">
              {signOutFailed && (
                <p className="mb-2 text-ui-sm leading-prose text-danger">
                  {SIGN_OUT_FAILED_SENTENCE}
                </p>
              )}
            </div>
            <Button size="sm" variant="ghost" fullWidth onClick={signOut}>
              Sign out
            </Button>
          </div>
        </div>
      }
    >
      {children}
    </AppShell>
  );
}
