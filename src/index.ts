#!/usr/bin/env node

import { Command } from 'commander';
import { unreadCommand } from './cli/unread.js';
import { syncCommand } from './cli/sync.js';
import { exportCommand } from './cli/export.js';
import { closeDatabase } from './storage/database.js';
import { listSites, getDefaultSite } from './config/credentials.js';

const program = new Command();

program
  .name('zulip-client')
  .description('Local Zulip message sync and unread management')
  .version('1.0.0');

program
  .command('unread')
  .description('Show unread message summary')
  .option('-s, --site <site>', `Zulip site (default: ${getDefaultSite()})`)
  .option('-a, --all', 'Show all configured sites')
  .action(async (options) => {
    try {
      await unreadCommand(options);
    } catch (error) {
      console.error('Error:', error instanceof Error ? error.message : error);
      process.exit(1);
    } finally {
      closeDatabase();
    }
  });

program
  .command('sync')
  .description('Download threads with unread messages')
  .option('-s, --site <site>', `Zulip site (default: ${getDefaultSite()})`)
  .option('-a, --all', 'Sync all configured sites')
  .option('-v, --verbose', 'Show detailed progress')
  .action(async (options) => {
    try {
      await syncCommand(options);
    } catch (error) {
      console.error('Error:', error instanceof Error ? error.message : error);
      process.exit(1);
    } finally {
      closeDatabase();
    }
  });

program
  .command('export')
  .description('Export stored messages to JSON or Markdown')
  .option('-s, --site <site>', `Zulip site (default: ${getDefaultSite()})`)
  .option('--stream <stream>', 'Filter by stream name')
  .option('--topic <topic>', 'Filter by topic name (requires --stream)')
  .option('-f, --format <format>', 'Output format: json or markdown', 'json')
  .action(async (options) => {
    try {
      if (options.topic && !options.stream) {
        console.error('Error: --topic requires --stream');
        process.exit(1);
      }
      if (options.format && !['json', 'markdown'].includes(options.format)) {
        console.error('Error: format must be "json" or "markdown"');
        process.exit(1);
      }
      await exportCommand(options);
    } catch (error) {
      console.error('Error:', error instanceof Error ? error.message : error);
      process.exit(1);
    } finally {
      closeDatabase();
    }
  });

program
  .command('sites')
  .description('List configured Zulip sites')
  .action(() => {
    const sites = listSites();
    const defaultSite = getDefaultSite();
    console.log('Configured sites:');
    for (const site of sites) {
      const marker = site === defaultSite ? ' (default)' : '';
      console.log(`  - ${site}${marker}`);
    }
  });

program.parse();
