// Build query_results.docx from a live run of the analysis.
//
// The JSON comes from tools/export_results_json.py, so every result in the
// document is one PostgreSQL actually returned rather than a figure typed in by
// hand.  Open the .docx in Word, adjust anything you want, then export to PDF --
// the same route limitations.pdf takes.
//
//     python tools/export_results_json.py --out results.json
//     node tools/build_query_results_docx.js results.json query_results.docx
//
// Needs the docx package:  npm install docx

const fs = require("fs");
const path = require("path");

const {
  AlignmentType,
  BorderStyle,
  Document,
  Footer,
  HeadingLevel,
  PageNumber,
  Packer,
  Paragraph,
  ShadingType,
  Table,
  TableCell,
  TableRow,
  TextRun,
  WidthType,
} = require("docx");

const INK = "1B1F24";
const SOFT = "4A5462";
const ACCENT = "1D4ED8";
const RULE = "DFE3E8";
const CODE_BG = "F4F6F8";
const CAVEAT_BG = "FFFBEB";

// US Letter in DXA; docx-js defaults to A4 otherwise.
const PAGE = { width: 12240, height: 15840 };
const MARGIN = 1080; // 0.75"
const CONTENT_WIDTH = PAGE.width - MARGIN * 2;

const [, , jsonPath, outPath] = process.argv;
if (!jsonPath || !outPath) {
  console.error(
    "usage: node build_query_results_docx.js <results.json> <output.docx>"
  );
  process.exit(2);
}

const data = JSON.parse(fs.readFileSync(jsonPath, "utf8"));

// The Python source writes "--" where it means an em dash, because the console
// it normally prints to cannot be relied on for anything but ASCII. Word has no
// such excuse, so promote them on the way in. SQL is left alone: "--" starts a
// comment there and must stay two hyphens.
function prose(text) {
  return typeof text === "string" ? text.replace(/ -- /g, " — ") : text;
}

/** A small caption above a block, in the accent colour. */
function label(text) {
  return new Paragraph({
    spacing: { before: 160, after: 60 },
    children: [
      new TextRun({ text, bold: true, size: 15, color: ACCENT, font: "Calibri" }),
    ],
  });
}

function body(text, options = {}) {
  return new Paragraph({
    spacing: { after: options.after === undefined ? 120 : options.after },
    alignment: AlignmentType.JUSTIFIED,
    children: [
      new TextRun({
        text,
        size: options.size || 20,
        color: options.color || INK,
        bold: options.bold || false,
        font: "Calibri",
      }),
    ],
  });
}

/** One shaded, bordered box holding monospace or tinted text. */
function boxed(lines, options = {}) {
  const paragraphs = lines.map(
    (line, index) =>
      new Paragraph({
        spacing: { after: index === lines.length - 1 ? 0 : 20 },
        children: [
          new TextRun({
            // A leading space is not preserved by Word unless the run keeps it,
            // so indentation is rebuilt with non-breaking spaces.
            text: line.replace(/^ +/, (run) => " ".repeat(run.length)),
            font: options.mono === false ? "Calibri" : "Consolas",
            size: options.size || 17,
            color: options.color || INK,
          }),
        ],
      })
  );

  return new Table({
    width: { size: CONTENT_WIDTH, type: WidthType.DXA },
    columnWidths: [CONTENT_WIDTH],
    borders: outlineBorders(options.border || RULE),
    rows: [
      new TableRow({
        children: [
          new TableCell({
            width: { size: CONTENT_WIDTH, type: WidthType.DXA },
            shading: {
              type: ShadingType.CLEAR,
              fill: options.fill || CODE_BG,
              color: "auto",
            },
            margins: { top: 120, bottom: 120, left: 160, right: 160 },
            children: paragraphs,
          }),
        ],
      }),
    ],
  });
}

function outlineBorders(color) {
  const side = { style: BorderStyle.SINGLE, size: 4, color };
  return { top: side, bottom: side, left: side, right: side };
}

/** A result table: header row plus data rows, numbers right-aligned. */
function resultTable(table) {
  const columnCount = table.columns.length;
  const firstWidth = Math.round(CONTENT_WIDTH * 0.34);
  const otherWidth = Math.round((CONTENT_WIDTH - firstWidth) / (columnCount - 1));
  const widths = [firstWidth];
  for (let i = 1; i < columnCount; i += 1) widths.push(otherWidth);
  // Column widths must sum to the table width or Word reflows them.
  const total = widths.reduce((sum, value) => sum + value, 0);
  widths[columnCount - 1] += CONTENT_WIDTH - total;

  const cell = (text, { header = false, index = 0 } = {}) =>
    new TableCell({
      width: { size: widths[index], type: WidthType.DXA },
      shading: header
        ? { type: ShadingType.CLEAR, fill: CODE_BG, color: "auto" }
        : undefined,
      margins: { top: 60, bottom: 60, left: 100, right: 100 },
      children: [
        new Paragraph({
          alignment: index === 0 ? AlignmentType.LEFT : AlignmentType.RIGHT,
          children: [
            new TextRun({
              text: String(text),
              bold: header,
              size: 16,
              color: header ? SOFT : INK,
              font: "Calibri",
            }),
          ],
        }),
      ],
    });

  const rows = [
    new TableRow({
      tableHeader: true,
      children: table.columns.map((column, index) =>
        cell(column, { header: true, index })
      ),
    }),
  ];
  for (const row of table.rows) {
    rows.push(
      new TableRow({ children: row.map((value, index) => cell(value, { index })) })
    );
  }

  return new Table({
    width: { size: CONTENT_WIDTH, type: WidthType.DXA },
    columnWidths: widths,
    borders: outlineBorders(RULE),
    rows,
  });
}

const children = [
  new Paragraph({
    heading: HeadingLevel.TITLE,
    spacing: { after: 60 },
    children: [
      new TextRun({ text: "Grad Cafe SQL Analysis", bold: true, size: 40, color: INK, font: "Calibri" }),
    ],
  }),
  new Paragraph({
    spacing: { after: 240 },
    children: [
      new TextRun({
        text: `${data.author} (${data.jhed})  ·  ${data.course}  ·  ${data.module}`,
        size: 19,
        color: SOFT,
        font: "Calibri",
      }),
    ],
  }),
  body(
    `Eleven questions answered against a PostgreSQL table of ${data.total_rows} Grad Cafe ` +
      "admissions results. Every query below was executed through psycopg by query_data.py, " +
      "and this document is generated from that run, so the result printed beside each query " +
      "is the one that query produced rather than a figure copied across by hand. Counts are " +
      "whole numbers; percentages and averages are given to two decimal places.",
    { color: SOFT, after: 200 }
  ),
];

for (const question of data.questions) {
  const heading =
    `Question ${question.number}` + (question.original ? " — my own question" : "");

  children.push(
    new Paragraph({
      heading: HeadingLevel.HEADING_2,
      spacing: { before: 320, after: 80 },
      children: [new TextRun({ text: heading, bold: true, size: 24, color: INK, font: "Calibri" })],
    })
  );
  children.push(body(prose(question.question), { bold: true }));

  children.push(label("RESULT"));
  children.push(boxed(question.answer_lines.map(prose), { size: 18 }));

  if (question.table) {
    children.push(new Paragraph({ spacing: { after: 80 }, children: [] }));
    children.push(resultTable(question.table));
  }

  if (question.supporting && question.supporting.length) {
    children.push(label("SUPPORTING ANALYSIS"));
    for (const entry of question.supporting) {
      children.push(
        new Paragraph({
          spacing: { after: 60 },
          bullet: { level: 0 },
          children: [new TextRun({ text: prose(entry), size: 19, color: INK, font: "Calibri" })],
        })
      );
    }
  }

  children.push(label("SQL"));
  children.push(boxed(question.sql.split("\n")));

  children.push(label("WHAT IT DOES"));
  children.push(body(prose(question.explanation)));

  if (question.caveat) {
    children.push(label("READ BEFORE QUOTING THIS NUMBER"));
    children.push(
      boxed([prose(question.caveat)], {
        mono: false,
        size: 18,
        fill: CAVEAT_BG,
        border: "FCD9A4",
        color: "7C3A06",
      })
    );
  }
}

const document = new Document({
  creator: data.author,
  title: "Grad Cafe SQL Analysis",
  styles: { default: { document: { run: { font: "Calibri", size: 20 } } } },
  sections: [
    {
      properties: {
        page: {
          size: { width: PAGE.width, height: PAGE.height },
          margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN },
        },
      },
      footers: {
        default: new Footer({
          children: [
            new Paragraph({
              alignment: AlignmentType.RIGHT,
              children: [
                new TextRun({
                  children: [`${data.author} (${data.jhed})    Page `, PageNumber.CURRENT],
                  size: 16,
                  color: SOFT,
                  font: "Calibri",
                }),
              ],
            }),
          ],
        }),
      },
      children,
    },
  ],
});

Packer.toBuffer(document).then((buffer) => {
  fs.mkdirSync(path.dirname(path.resolve(outPath)), { recursive: true });
  fs.writeFileSync(outPath, buffer);
  console.log(`wrote ${outPath}`);
});
