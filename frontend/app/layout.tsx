import './globals.css';
import Link from 'next/link';
import { LogoutButton } from '../components/LogoutButton';

const links = [
  ['Dashboard', '/dashboard'],
  ['Companies', '/companies'],
  ['Sales Campaigns', '/campaigns'],
  ['Leads', '/campaigns'],
  ['Jobs', '/jobs'],
  ['Logs', '/reports'],
  ['Health', '/system'],
];

export default function RootLayout({children}:{children:React.ReactNode}){return <html><head><script src="/voryx-action-runtime.js" defer /></head><body><div className="min-h-screen"><nav className="border-b border-zinc-800 bg-zinc-950/90 px-5 py-3"><div className="flex flex-wrap items-center gap-4"><b className="mr-2 text-white">Voryx Ops</b>{links.map(([label, href])=><Link className="text-sm text-zinc-300 hover:text-white" href={href} key={href}>{label}</Link>)}<LogoutButton /></div></nav><main className="p-5 lg:p-6">{children}</main></div></body></html>}
