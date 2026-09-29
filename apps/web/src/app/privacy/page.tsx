import type { Metadata } from 'next';
import { PrivacyPolicy } from '@/components/legal/PrivacyPolicy';

/**
 * /privacy — public, 2026-09-29.
 *
 * The URL entered in Google Cloud → Branding as the privacy policy, so the
 * OAuth consent screen can leave Testing. No session call on this route or
 * anywhere under it: a signed-out visitor, and Google's review, get the page
 * as it is, never a redirect. Draft for the founder's review — see
 * `components/legal/PrivacyPolicy.tsx`.
 */
export const metadata: Metadata = {
  title: 'Privacy — AI Visibility Platform',
  description: 'What AI Visibility Platform stores, who else handles it, and how to have it removed.',
};

export default function PrivacyRoute() {
  return <PrivacyPolicy />;
}
