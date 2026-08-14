import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

const [specPath, outputPath, moduleName = "API"] = process.argv.slice(2);
if (!specPath || !outputPath) {
  throw new Error("usage: render_scalar_api_docs.mjs <spec.json> <output.html> [module]");
}

const spec = JSON.parse(await readFile(specPath, "utf8"));
const embeddedSpec = JSON.stringify(spec).replace(/</g, "\\u003c");
const title = spec.info?.title ?? `Kairos ${moduleName} API`;

const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${title}</title>
    <style>html, body, #app { margin: 0; min-height: 100%; width: 100%; }</style>
  </head>
  <body>
    <div id="app"></div>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.35.7"></script>
    <script>
      const spec = ${embeddedSpec};
      Scalar.createApiReference('#app', {
        spec: { content: spec },
        hideClientButton: true,
        hideModels: false,
        darkMode: false,
        theme: 'default'
      });
    </script>
  </body>
</html>
`;

await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, html);
