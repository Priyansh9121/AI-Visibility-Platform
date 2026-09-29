import type { Metadata } from 'next';
import { TermsOfService } from '@/components/legal/TermsOfService';

/**
 * /terms — public, 2026-09-29.
 *
 * The terms-of-service URL entered in Google Cloud → Branding beside
 * /privacy. No session call on this route or anywhere under it. Draft for the
 * founder's review — see `components/legal/TermsOfService.tsx`.
 */
export const metadata: Metadata = {
  title: 'Terms — AI Visibility Platform',
  description: 'The terms of using AI Visibility Platform: the plan, the free scan, cancelling, and fair use.',
};

export default function TermsRoute() {
  return <TermsOfService />;
}
