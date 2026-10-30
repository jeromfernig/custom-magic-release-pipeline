// A local, single-card bridge. Art is supplied as bytes, never fetched from URLs.
const fs = require('node:fs');

async function main() {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const { renderCard } = require(input.modulePath);
  const warnings = [];
  console.warn = (...args) => warnings.push(args.map(String).join(' '));
  console.log = (...args) => warnings.push(args.map(String).join(' '));
  const card = {
    ...input.card,
    artUrl: Buffer.from(input.artBase64, 'base64'),
  };
  if (card.linkedCard && input.linkedArtBase64) {
    card.linkedCard.artUrl = Buffer.from(input.linkedArtBase64, 'base64');
  }
  const result = await renderCard(card, { quality: input.quality, format: 'jpeg' });
  if (warnings.some(line => line.includes('Failed to load art'))) {
    throw new Error(warnings.filter(line => line.includes('Failed to load art')).join('; '));
  }
  process.stdout.write(JSON.stringify({ image: Buffer.from(result.frontFace).toString('base64'),
    back: result.backFace ? Buffer.from(result.backFace).toString('base64') : undefined }));
}

main().catch(error => {
  process.stderr.write(String(error.message || error));
  process.exitCode = 1;
});
