import { readFileSync, existsSync } from 'fs';
import { homedir } from 'os';
import { join } from 'path';

export interface SiteCredentials {
  email: string;
  api_key: string;
  site: string;
}

export interface CredentialsFile {
  default: string;
  sites: Record<string, SiteCredentials>;
}

const CREDENTIALS_PATH = join(homedir(), 'metacortex', '.credentials', 'zulip.json');

let cachedCredentials: CredentialsFile | null = null;

export function loadCredentials(): CredentialsFile {
  if (cachedCredentials) {
    return cachedCredentials;
  }

  if (!existsSync(CREDENTIALS_PATH)) {
    throw new Error(`Credentials file not found at ${CREDENTIALS_PATH}`);
  }

  const content = readFileSync(CREDENTIALS_PATH, 'utf-8');
  cachedCredentials = JSON.parse(content) as CredentialsFile;
  return cachedCredentials;
}

export function getSiteCredentials(siteName?: string): SiteCredentials {
  const credentials = loadCredentials();
  const site = siteName || credentials.default;

  if (!credentials.sites[site]) {
    const available = Object.keys(credentials.sites).join(', ');
    throw new Error(`Site '${site}' not found. Available sites: ${available}`);
  }

  return credentials.sites[site];
}

export function getDefaultSite(): string {
  return loadCredentials().default;
}

export function listSites(): string[] {
  return Object.keys(loadCredentials().sites);
}
