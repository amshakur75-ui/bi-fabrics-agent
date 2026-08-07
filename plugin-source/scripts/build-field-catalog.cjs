/**
 * build-field-catalog.cjs
 *
 * Build-time processor for the field catalog. Reads the raw 14MB
 * data/enriched-field-catalog.json (20,283 records, 13 short-named models)
 * plus the Dim Catalog CSV/xlsx enrichment files, and emits:
 *
 *   data/catalog/manifest.json          — version, counts, model list
 *   data/catalog/models/<Canonical>.json — trimmed per-model record files
 *   data/catalog/search-index.json      — flat field list + token postings
 *
 * Why: the runtime previously parsed all 14MB at startup and used only 2 of
 * 15 fields per record. Pre-splitting lets the server load ~1-2MB eagerly
 * and lazy-load model detail on demand; the search index is byte-compatible
 * with schema-link.ts's tokenizer (lowercase, split on whitespace/_/-, over
 * field + sourceColumn only) so Pass 1c behavior is identical.
 *
 * Transforms:
 *   - model names rewritten to canonical routing-table names (MODEL_MAP —
 *     keep in sync with routing-table.ts catalogModelName fields; the build
 *     FAILS on an unmapped catalog model rather than guessing)
 *   - fieldType casing normalized to lowercase ("Column" -> "column";
 *     null preserved — all 1,628 SLM records ship without a fieldType)
 *   - searchText dropped (derivable; pure dead weight at runtime)
 *   - Dim Catalog CSVs merged: curated definitions fill missing
 *     descriptions, cross-system equivalents (OBIEE/Luminate/DSS/E2open)
 *     collected into crossSystemEquivalents, PurFin calculations filled
 *   - Ent-Reporting-DTC synthesized entirely from Z.DTC Data Dictionary.csv
 *     (the only DTC field metadata that exists anywhere)
 *
 * Usage: node scripts/build-field-catalog.cjs [--dim-catalog <dir>]
 *   Default dim-catalog dir: C:\Users\HJ45676\Downloads\Dim Catalog
 *   Rerun whenever the raw catalog or the CSVs change, then rebuild dist.
 */

"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const RAW_CATALOG = path.join(ROOT, "data", "enriched-field-catalog.json");
const OUT_DIR = path.join(ROOT, "data", "catalog");
const dimArgIdx = process.argv.indexOf("--dim-catalog");
// Searched in order; the first copy of a file that actually contains data
// rows wins. "Gapped Dims" holds full re-exports of the lists whose
// "Dim Catalog" copies came out schema-only (0 data rows).
const DIM_DIRS = dimArgIdx !== -1
  ? [process.argv[dimArgIdx + 1]]
  : [
      "C:\\Users\\HJ45676\\Downloads\\Gapped Dims",
      "C:\\Users\\HJ45676\\Downloads\\Dim Catalog",
    ];

// Catalog short name -> canonical routing-table name.
// KEEP IN SYNC with routing-table.ts catalogModelName fields.
const MODEL_MAP = {
  "Z_Sales":            "Ent-Reporting-Sales",
  "Ecomm":              "Ent-Reporting-Ecomm",
  "Finance":            "Ent-Reporting-Finance",
  "Purchasing-Finance": "Ent-Reporting-Purchasing-Finance",
  "Profitability":      "Ent-Reporting-Profitability",
  "Z_Marketing":        "Ent-Reporting-Marketing",
  "Z_OpsFin":           "Ent-Reporting-Ops-Finance",
  "Z_Quality":          "Ent-Reporting-Quality",
  "SCM":                "Ent-Reporting-SCM",
  "SLM":                "Ent-Reporting-SLM",
  "Walmart":            "Ent-Reporting-Walmart",
  "CMMS":               "CMMS",
  "OEE":                "OEE Monthly Reports",
};

// ── CSV enrichment source descriptors ─────────────────────────────────────────
// Each maps a Dim Catalog file to a canonical model and tells the merger which
// columns carry what. `fieldCol` is the field-name column; `tableCol` optional
// (absent = match by field name alone, only when unique within the model).
// `equivCols` map cross-system columns to a system label.
const CSV_SOURCES = [
  {
    // Schema-only export headers this "Z.DTC Field Name"; the full Gapped
    // Dims re-export headers it "Field Name" — accept both.
    file: "Z.DTC Data Dictionary.csv", model: "Ent-Reporting-DTC", synthesize: true,
    fieldCol: ["Z.DTC Field Name", "Field Name"], tableCol: "Table", typeCol: "Field Type",
    descCol: "Field Definition", exampleCol: "Example Values", detailsCol: "Additional Details",
    folderCol: "Folder", equivCols: { "Oracle OBIEE Equivalent": "Oracle OBIEE" },
  },
  {
    file: "Z.Sales Data Dictionary.csv", model: "Ent-Reporting-Sales",
    fieldCol: "Name", tableCol: "Table", typeCol: "Field Type",
    descCol: "Field Definition", exampleCol: "Example Values", detailsCol: "Additional Details",
  },
  {
    file: "Z.eComm Data Dictionary (2).csv", model: "Ent-Reporting-Ecomm",
    fieldCol: "Field Name", tableCol: "Table", typeCol: "Field Type",
    descCol: "Field Definition", exampleCol: "Example Values", detailsCol: "Additional Details",
  },
  {
    file: "Z.OpsFin Data Dictionary.csv", model: "Ent-Reporting-Ops-Finance",
    fieldCol: "Field Name", tableCol: "Table", typeCol: "Field Type",
    descCol: "Field Definition", exampleCol: "Example Values", detailsCol: "Additional Details",
  },
  {
    file: "Z.Walmart Data Dictionary.csv", model: "Ent-Reporting-Walmart",
    fieldCol: "Title", tableCol: "Table", typeCol: "Field Type",
    descCol: "Field Definition", exampleCol: "Example Values", detailsCol: "Additional Details",
    equivCols: {
      "Luminate Equivalent": "Walmart Luminate",
      "DSS Equivalent": "DSS",
      "E2open Equivalent": "E2open",
    },
  },
  {
    file: "Z.Finance Dimension Catalog.csv", model: "Ent-Reporting-Finance",
    fieldCol: "Characteristic", tableCol: "Table",
    descCol: "Description", exampleCol: "Example Value",
  },
  {
    file: "Z.SCM Dimension Catalog.csv", model: "Ent-Reporting-SCM",
    fieldCol: "Dimensions", tableCol: "Table",
    descCol: "Description", exampleCol: "Example Value",
  },
  {
    file: "Z.SCM Measure Catalog (1).csv", model: "Ent-Reporting-SCM",
    fieldCol: "Facts", descCol: "Description",
  },
  {
    file: "Z.Marketing Dimensions Catalog.csv", model: "Ent-Reporting-Marketing",
    fieldCol: "Dimension Name", tableCol: "Table Name", descCol: "Field Definition",
  },
  {
    file: "Z.Marketing Facts Catalog.csv", model: "Ent-Reporting-Marketing",
    fieldCol: "Fact Name", tableCol: "Fact Table", descCol: "Description",
  },
  {
    file: "Z.PurFin Attributes.csv", model: "Ent-Reporting-Purchasing-Finance",
    fieldCol: "Title", tableCol: "Table Name ", descCol: "Description",
    exampleCol: "Example", calcCol: "Calculation",
  },
  {
    file: "Z.PurFin Measures.xlsx", model: "Ent-Reporting-Purchasing-Finance", xlsx: true,
    fieldCol: "Field Name", tableCol: "Table Name ", descCol: "Description",
    exampleCol: "Example", calcCol: "Calculation",
  },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

// Mirrors schema-link.ts tokenize() exactly — parity is load-bearing.
function tokenize(input) {
  return input.toLowerCase().split(/[\s_-]+/).filter(t => t.length > 0);
}

function normKey(s) {
  return String(s ?? "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

// Minimal RFC-4180 CSV parser (quotes, embedded commas/newlines).
function parseCsv(text) {
  const rows = [];
  let row = [], cell = "", inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { cell += '"'; i++; }
        else inQuotes = false;
      } else cell += c;
    } else if (c === '"') inQuotes = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); cell = "";
      if (row.length > 1 || row[0] !== "") rows.push(row);
      row = [];
    } else cell += c;
  }
  if (cell !== "" || row.length > 0) { row.push(cell); if (row.length > 1 || row[0] !== "") rows.push(row); }
  return rows;
}

// SharePoint exports carry a giant ListSchema= blob as line 1; the real
// header is the first row whose cells look like quoted column names.
function loadSharePointCsv(filePath) {
  const raw = fs.readFileSync(filePath, "utf8").replace(/^﻿/, "");
  const firstNl = raw.indexOf("\n");
  const body = raw.startsWith("ListSchema") || raw.slice(0, firstNl).includes("ListSchema")
    ? raw.slice(firstNl + 1)
    : raw;
  const rows = parseCsv(body);
  if (rows.length < 2) return [];
  const header = rows[0].map(h => h.trim());
  return rows.slice(1).map(r => {
    const obj = {};
    header.forEach((h, i) => { obj[h] = (r[i] ?? "").trim(); });
    return obj;
  });
}

function loadXlsxRows(filePath) {
  const xlsx = require(path.join(ROOT, "node_modules", "xlsx"));
  const wb = xlsx.readFile(filePath);
  return xlsx.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { defval: "" });
}

function clean(v) {
  const s = String(v ?? "").trim();
  return s === "" ? null : s;
}

function safeFileName(model) {
  return model.replace(/[^A-Za-z0-9-]+/g, "_");
}

// ── 1. Load and transform the raw catalog ─────────────────────────────────────

console.log("Reading raw catalog...");
const raw = JSON.parse(fs.readFileSync(RAW_CATALOG, "utf8"));
console.log(`  ${raw.length} records`);

const unmapped = new Set();
const byModel = new Map(); // canonical -> records
for (const rec of raw) {
  const canonical = MODEL_MAP[rec.model];
  if (canonical === undefined) { unmapped.add(rec.model); continue; }
  const out = {
    model: canonical,
    table: rec.table,
    field: rec.field,
    fieldType: rec.fieldType == null ? null : String(rec.fieldType).toLowerCase(),
    dataType: clean(rec.dataType),
    sourceColumn: clean(rec.sourceColumn),
    displayFolder: clean(rec.displayFolder),
    description: clean(rec.description) ?? clean(rec.definition),
    exampleValues: clean(rec.exampleValues),
    additionalDetails: clean(rec.additionalDetails),
    calculation: clean(rec.calculation) ?? clean(rec.expression),
  };
  if (!byModel.has(canonical)) byModel.set(canonical, []);
  byModel.get(canonical).push(out);
}
if (unmapped.size > 0) {
  console.error(`FATAL: unmapped catalog models: ${[...unmapped].join(", ")} — add them to MODEL_MAP (and routing-table.ts catalogModelName).`);
  process.exit(1);
}

// ── 2. Merge Dim Catalog enrichments ──────────────────────────────────────────

const mergeStats = [];
for (const src of CSV_SOURCES) {
  // First dir whose copy has data rows wins (schema-only exports have <3 newlines).
  let filePath = null;
  for (const dir of DIM_DIRS) {
    const p = path.join(dir, src.file);
    if (!fs.existsSync(p)) continue;
    if (src.xlsx || (fs.readFileSync(p, "utf8").match(/\r\n|\r|\n/g) || []).length > 2) { filePath = p; break; }
    if (filePath === null) filePath = p; // schema-only fallback, keep looking
  }
  if (filePath === null) {
    mergeStats.push(`${src.file}: MISSING (skipped)`);
    continue;
  }
  const rows = src.xlsx ? loadXlsxRows(filePath) : loadSharePointCsv(filePath);
  const records = byModel.get(src.model) ?? [];
  if (!byModel.has(src.model)) byModel.set(src.model, records);

  // Index existing records for the model: by (table|field) and by field alone.
  const byTableField = new Map();
  const byField = new Map();
  for (const r of records) {
    byTableField.set(`${normKey(r.table)}|${normKey(r.field)}`, r);
    const fk = normKey(r.field);
    if (!byField.has(fk)) byField.set(fk, []);
    byField.get(fk).push(r);
  }

  let enriched = 0, created = 0, unmatchedRows = 0;
  for (const row of rows) {
    const fieldCols = Array.isArray(src.fieldCol) ? src.fieldCol : [src.fieldCol];
    const fieldName = fieldCols.map(c => clean(row[c])).find(v => v !== null) ?? null;
    if (fieldName === null) continue;
    const tableName = src.tableCol ? clean(row[src.tableCol]) : null;

    // Find the target record.
    let target = null;
    if (tableName !== null) target = byTableField.get(`${normKey(tableName)}|${normKey(fieldName)}`) ?? null;
    if (target === null) {
      const candidates = byField.get(normKey(fieldName)) ?? [];
      if (candidates.length === 1) target = candidates[0];
    }

    if (target === null) {
      if (src.synthesize) {
        target = {
          model: src.model,
          table: tableName ?? "Unknown",
          field: fieldName,
          fieldType: src.typeCol ? (clean(row[src.typeCol])?.toLowerCase() ?? null) : null,
          dataType: null, sourceColumn: null,
          displayFolder: src.folderCol ? clean(row[src.folderCol]) : null,
          description: null, exampleValues: null, additionalDetails: null, calculation: null,
        };
        records.push(target);
        byTableField.set(`${normKey(target.table)}|${normKey(fieldName)}`, target);
        // Register in the field-only index too, or a later row with the same
        // field name and a blank Table would synthesize a duplicate record.
        const fk = normKey(fieldName);
        if (!byField.has(fk)) byField.set(fk, []);
        byField.get(fk).push(target);
        created++;
      } else {
        unmatchedRows++;
        continue;
      }
    }

    // Curated CSV values fill gaps; they do not overwrite existing catalog data
    // except description, where human-curated text wins over generated text.
    const desc = src.descCol ? clean(row[src.descCol]) : null;
    if (desc !== null) { target.description = desc; target.curated = true; }
    if (src.exampleCol && target.exampleValues === null) target.exampleValues = clean(row[src.exampleCol]);
    if (src.detailsCol && target.additionalDetails === null) target.additionalDetails = clean(row[src.detailsCol]);
    if (src.calcCol && target.calculation === null) target.calculation = clean(row[src.calcCol]);
    if (src.equivCols) {
      for (const [col, system] of Object.entries(src.equivCols)) {
        const v = clean(row[col]);
        if (v !== null) {
          if (!target.crossSystemEquivalents) target.crossSystemEquivalents = {};
          target.crossSystemEquivalents[system] = v;
        }
      }
    }
    enriched++;
  }
  mergeStats.push(`${src.file}: ${enriched} enriched, ${created} created, ${unmatchedRows} unmatched`);
}

// ── 3. Emit outputs ───────────────────────────────────────────────────────────

fs.mkdirSync(path.join(OUT_DIR, "models"), { recursive: true });

const models = [...byModel.keys()].sort();
const fields = [];   // flat: {i: id implied by position, m, t, f, y(type)}
const postings = {}; // token -> id[]

for (const model of models) {
  const records = byModel.get(model);
  // Per-model file: strip nulls to keep files lean.
  const trimmed = records.map(r => {
    const o = { model: r.model, table: r.table, field: r.field };
    for (const k of ["fieldType", "dataType", "sourceColumn", "displayFolder", "description", "exampleValues", "additionalDetails", "calculation", "crossSystemEquivalents", "curated"]) {
      if (r[k] != null) o[k] = r[k];
    }
    return o;
  });
  fs.writeFileSync(path.join(OUT_DIR, "models", `${safeFileName(model)}.json`), JSON.stringify(trimmed));

  for (const r of records) {
    if (!r.field) continue;
    const id = fields.length;
    fields.push({ m: model, t: r.table, f: r.field, y: r.fieldType ?? null });
    const tokens = new Set([
      ...tokenize(r.field),
      ...(r.sourceColumn ? tokenize(r.sourceColumn) : []),
    ]);
    for (const tok of tokens) {
      (postings[tok] ??= []).push(id);
    }
  }
}

fs.writeFileSync(path.join(OUT_DIR, "search-index.json"), JSON.stringify({ fields, postings }));

const manifest = {
  schemaVersion: "1.0.0",
  builtFrom: path.basename(RAW_CATALOG),
  models: models.map(m => ({
    name: m,
    file: `models/${safeFileName(m)}.json`,
    records: byModel.get(m).length,
    withDescription: byModel.get(m).filter(r => r.description != null).length,
  })),
  totalRecords: fields.length,
  distinctTokens: Object.keys(postings).length,
};
fs.writeFileSync(path.join(OUT_DIR, "manifest.json"), JSON.stringify(manifest, null, 2));

console.log("Merge results:");
for (const s of mergeStats) console.log(`  ${s}`);
console.log(`Models: ${models.length}, total records: ${fields.length}, tokens: ${manifest.distinctTokens}`);
const sizes = fs.readdirSync(path.join(OUT_DIR, "models")).map(f => fs.statSync(path.join(OUT_DIR, "models", f)).size).reduce((a, b) => a + b, 0);
console.log(`Output size: models ${(sizes / 1e6).toFixed(1)}MB, index ${(fs.statSync(path.join(OUT_DIR, "search-index.json")).size / 1e6).toFixed(1)}MB`);
console.log("Done.");
