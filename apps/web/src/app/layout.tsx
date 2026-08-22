import type { Metadata } from 'next';
import type { ReactNode } from 'react';
// Imported from the tokens subpath, not the package barrel: the barrel pulls
// in client components, which would force this Server Component to bundle them.
import { GOOGLE_FONTS_HREF } from '@avp/design-system/tokens';
import './globals.css';

export const metadata: Metadata = {
  title: 'AI Visibility Platform',
  description: 'See how a brand appears when buyers ask AI assistants for a recommendation.',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        {/* Fraunces + IBM Plex Sans/Mono, all OFL-1.1 (docs/ip-safety.md #4).
            The href is exported by the design system so fonts are declared in
            exactly one place. */}
        <link href={GOOGLE_FONTS_HREF} rel="stylesheet" />
      </head>
      <body>{children}</body>
    </html>
  );
}
