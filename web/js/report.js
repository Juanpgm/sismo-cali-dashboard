// Per-record PDF report (see openspec/changes/informe-pdf-registros).
// buildReportDocDefinition stays pure: no fetch/DOM/network. Asset gathering
// (photos/firmas/map) and pdfmake wiring land in a follow-up PR.
import {
  DETAIL_GROUPS, labelForField, formatValue, barrioVeredaDisplay, downloadStamp,
} from './utils.js';

export const MAX_PHOTOS = 12;

const DISCLAIMER = 'Informe generado automáticamente a partir de los registros del EDE. '
  + 'No sustituye la evaluación técnica original ni constituye un documento oficial certificado.';

/** {dataURL, sourceUrl} -> pdfmake image node, or a "no disponible" placeholder
 *  with a link back to the source when dataURL is missing. */
function imageOrPlaceholder(asset, { width } = {}) {
  if (asset && asset.dataURL) {
    return width ? { image: asset.dataURL, width } : { image: asset.dataURL, fit: [220, 220] };
  }
  const sourceUrl = asset && asset.sourceUrl;
  return {
    stack: [
      { text: 'Imagen no disponible', italics: true, color: '#888', margin: [0, 0, 0, 2] },
      ...(sourceUrl ? [{ text: sourceUrl, link: sourceUrl, color: '#2a5db0', fontSize: 8 }] : []),
    ],
    margin: [0, 0, 0, 8],
  };
}

function buildHeader(record) {
  const codigo = record?.codigo || record?.ObjectID || 'Sin código';
  return [
    { text: 'Informe de inspección EDE', style: 'title' },
    { text: `Código / ObjectID: ${codigo}`, style: 'subtitle' },
    { text: `Fecha de generación: ${downloadStamp().legible}`, style: 'subtitle' },
    { text: DISCLAIMER, style: 'disclaimer', margin: [0, 4, 0, 12] },
  ];
}

/** One section per DETAIL_GROUPS entry that has at least one populated field
 *  in `record`, mirroring the on-screen detail modal exactly (empty fields —
 *  and thus empty groups — are omitted rather than shown as "Sin dato"). */
function buildFieldSections(record) {
  const sections = [];
  for (const [group, fields] of Object.entries(DETAIL_GROUPS)) {
    const populated = fields.filter((f) => {
      const v = record?.[f];
      return v !== null && v !== undefined && v !== '';
    });
    if (!populated.length) continue;
    sections.push({ text: group, style: 'sectionHeader' });
    sections.push({
      table: {
        widths: ['40%', '60%'],
        body: populated.map((f) => [
          { text: labelForField(f), style: 'fieldLabel' },
          { text: f === 'barrio_vereda_resuelto' ? barrioVeredaDisplay(record) : formatValue(f, record[f]), style: 'fieldValue' },
        ]),
      },
      layout: 'lightHorizontalLines',
      margin: [0, 0, 0, 10],
    });
  }
  return sections;
}

function buildImagesSection(title, assets, emptyText) {
  const list = Array.isArray(assets) ? assets : [];
  const section = [{ text: title, style: 'sectionHeader' }];
  if (!list.length) {
    section.push({ text: emptyText, italics: true, color: '#888', margin: [0, 0, 0, 10] });
    return section;
  }
  const shown = list.slice(0, MAX_PHOTOS);
  const overflow = list.length - shown.length;
  section.push({ columns: shown.map((a) => imageOrPlaceholder(a)), columnGap: 8, margin: [0, 0, 0, 4] });
  if (overflow > 0) {
    section.push({ text: `${overflow} fotos adicionales no incluidas`, italics: true, color: '#888', margin: [0, 0, 0, 10] });
  } else {
    section.push({ text: '', margin: [0, 0, 0, 10] });
  }
  return section;
}

function buildMapSection(mapImage) {
  const asset = mapImage && mapImage.dataURL
    ? { dataURL: mapImage.dataURL, sourceUrl: mapImage.sourceUrl }
    : { dataURL: null, sourceUrl: mapImage && mapImage.sourceUrl };
  return [
    { text: 'Ubicación', style: 'sectionHeader' },
    imageOrPlaceholder(asset, { width: 300 }),
  ];
}

/**
 * Pure builder: record + resolved assets -> pdfmake document definition.
 * No I/O — `photos`/`signatures`/`mapImage` must already be resolved
 * (dataURL or null+sourceUrl) by the Phase 2 asset-gathering step.
 */
export function buildReportDocDefinition(record, { photos, signatures, mapImage } = {}) {
  return {
    content: [
      ...buildHeader(record),
      ...buildFieldSections(record),
      ...buildImagesSection('Fotos', photos, 'Sin fotos en el survey.'),
      ...buildImagesSection('Firmas', signatures, 'Sin firmas en el survey.'),
      ...buildMapSection(mapImage),
    ],
    styles: {
      title: { fontSize: 16, bold: true },
      subtitle: { fontSize: 9, color: '#555' },
      disclaimer: { fontSize: 8, italics: true, color: '#777' },
      sectionHeader: { fontSize: 12, bold: true, margin: [0, 10, 0, 4] },
      fieldLabel: { fontSize: 9, bold: true },
      fieldValue: { fontSize: 9 },
    },
    defaultStyle: { fontSize: 9 },
  };
}
