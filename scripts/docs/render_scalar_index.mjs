import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

const [outputPath, ...pages] = process.argv.slice(2);
if (!outputPath) {
  throw new Error("usage: render_scalar_index.mjs <output.html> [page.html...]");
}

const links =
  pages.length === 0
    ? "<li>No OpenAPI control schemas are active.</li>"
    : pages
        .map((page) => {
          const name = page.replace(/\.html$/, "");
          const label = name.charAt(0).toUpperCase() + name.slice(1);
          return `<li><a href="${page}">${label} API</a></li>`;
        })
        .join("\n");

const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Kairos API documentation</title>
    <style>
      body { font: 16px system-ui, sans-serif; margin: 3rem auto; max-width: 48rem; padding: 0 1rem; }
      li { margin: 0.75rem 0; }
      a { color: #0969da; }
    </style>
  </head>
  <body>
    <h1>Kairos API documentation</h1>
    <p>Static Scalar references generated from active v2 OpenAPI schemas.</p>
    <ul>${links}</ul>
  </body>
</html>
`;

await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, html);
