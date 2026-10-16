// Open only a disposable PGLite snapshot. No GBrain migrations, models or APIs.
import { mkdirSync, writeFileSync, appendFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

if (process.argv.length !== 5) throw new Error('source, disposable database and output required');
const [source, database, output] = process.argv.slice(2).map(value => resolve(value));
const dist = join(source, 'node_modules/@electric-sql/pglite/dist');
const { PGlite } = await import(pathToFileURL(join(dist, 'index.js')).href);
const { vector } = await import(pathToFileURL(join(dist, 'vector/index.js')).href);
const { pg_trgm } = await import(pathToFileURL(join(dist, 'contrib/pg_trgm.js')).href);
const db = new PGlite(database, { extensions: { vector, pg_trgm } });
const id = (value: string) => createHash('sha256').update(value).digest('hex');
const quote = (value: string) => '"' + value.replaceAll('"', '""') + '"';
const counts: Record<string, number> = {};
const pages: Record<string, unknown>[] = [];
try {
  await db.waitReady;
  await db.exec('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');
  const tables = await db.query("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename");
  if (!tables.rows.some((row: { tablename: string }) => row.tablename === 'pages')) {
    throw new Error('Snapshot has no pages table; refusing an empty or unrelated database');
  }
  mkdirSync(join(output, 'tables'), { recursive: true });
  for (const { tablename: table } of tables.rows as { tablename: string }[]) {
    const file = join(output, 'tables', id(table) + '.jsonl');
    writeFileSync(file, '');
    let count = 0;
    for (;;) {
      const result = await db.query(`SELECT * FROM ${quote(table)} ORDER BY ctid LIMIT 500 OFFSET ${count}`);
      for (const row of result.rows as Record<string, unknown>[]) {
        appendFileSync(file, JSON.stringify(row) + '\n');
        if (table === 'pages') {
          // Hash both identities: duplicate slugs across sources cannot overwrite.
          const name = id(String(row.source_id)) + '/' + id(String(row.slug)) + '.md';
          const target = join(output, 'pages', name);
          mkdirSync(join(output, 'pages', id(String(row.source_id))), { recursive: true });
          const body = [row.compiled_truth, row.timeline].filter(Boolean).join('\n\n');
          writeFileSync(target, `# ${row.title ?? row.slug}\n\n${body}\n`);
          pages.push({ source_id: row.source_id, slug: row.slug, deleted_at: row.deleted_at, path: 'pages/' + name });
        }
      }
      count += result.rows.length;
      if (result.rows.length < 500) break;
    }
    counts[table] = count;
  }
  await db.exec('COMMIT');
  writeFileSync(join(output, 'index.json'), JSON.stringify({ tables: Object.entries(counts).map(([table, rows]) => ({ table, rows, path: 'tables/' + id(table) + '.jsonl' })), pages }, null, 2) + '\n');
  console.log(`Exported ${pages.length} pages (including soft-deleted pages) and ${Object.keys(counts).length} tables.`);
} finally {
  await db.close();
}
