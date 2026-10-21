import type { Metadata } from 'next';
import './style.css';

export const metadata: Metadata = { title: 'Pipeline Console', description: 'Local survey processing workspace' };
export default function Layout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
