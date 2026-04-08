#!/usr/bin/env node
'use strict';

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.error('jsdom not found. Install with: npm install jsdom');
  process.exit(1);
}

const vm = require('vm');
const fs = require('fs');

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--url' && argv[i + 1]) {
      args.url = argv[++i];
    } else if (argv[i] === '--js' && argv[i + 1]) {
      args.js = argv[++i];
    } else if (argv[i] === '--html' && argv[i + 1]) {
      args.html = argv[++i];
    }
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  // --url is required only when --html is absent (live fetch). When --html is
  // provided, --url is optional and used solely as the base URI for relative
  // link resolution; omitting it causes jsdom to use "about:blank" as base.
  if (!args.html && !args.url) {
    console.error('Error: --url is required when --html is not provided');
    process.exit(1);
  }
  if (!args.js) {
    console.error('Error: --js is required');
    process.exit(1);
  }

  let html;
  if (args.html) {
    try {
      html = fs.readFileSync(args.html, 'utf8');
    } catch (e) {
      console.error(`Error reading HTML file "${args.html}": ${e.message}`);
      process.exit(1);
    }
  } else {
    try {
      const response = await fetch(args.url);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status} ${response.statusText}`);
      }
      html = await response.text();
    } catch (e) {
      console.error(`Error fetching URL "${args.url}": ${e.message}`);
      process.exit(1);
    }
  }

  let dom;
  try {
    dom = new JSDOM(html, {
      url: args.url,
      runScripts: 'outside-only',
    });
  } catch (e) {
    console.error(`Error creating DOM: ${e.message}`);
    process.exit(1);
  }

  const window = dom.window;
  const context = vm.createContext(window);

  let result;
  try {
    result = vm.runInContext(args.js, context);
  } catch (e) {
    console.error(`Error running JS snippet: ${e.message}`);
    process.exit(1);
  }

  try {
    // Use `result ?? null` so undefined/null both serialize to JSON "null"
    // rather than JS undefined, which would cause console.log to print the
    // literal text "undefined" — unparseable by Python's json.loads().
    console.log(JSON.stringify(result ?? null));
  } catch (e) {
    console.error(`Error serializing result to JSON: ${e.message}`);
    process.exit(1);
  }
}

main().catch((e) => { console.error(`Unexpected error: ${e.message}`); process.exit(1); });
