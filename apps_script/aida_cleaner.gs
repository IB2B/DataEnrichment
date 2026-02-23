/**
 * ============================================
 * AIDA Export Cleaner — Google Apps Script
 * ============================================
 *
 * HOW TO USE:
 * 1. Open your Google Sheet
 * 2. Go to Extensions > Apps Script
 * 3. Delete any existing code and paste this entire script
 * 4. Click Save
 * 5. Go back to your sheet — you'll see a new menu "AIDA Cleaner"
 * 6. Click "AIDA Cleaner" > "Clean & Consolidate Data"
 * 7. Authorize the script when prompted
 *
 * WHAT IT DOES:
 * - Detects each company block (company row + its DM sub-rows)
 * - Consolidates all DM entries into one row per company (semicolon-separated)
 * - Keeps empty cells blank for future enrichment
 * - Outputs clean data to a new sheet called "Cleaned_Data"
 */

// ─── Add custom menu on open ───────────────────────────────────────────────────
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('AIDA Cleaner')
    .addItem('Clean & Consolidate Data', 'cleanAidaExport')
    .addItem('Clean in Place (overwrites!)', 'cleanAidaExportInPlace')
    .addToUi();
}

// ─── Main cleaning function (outputs to new sheet) ─────────────────────────────
function cleanAidaExport() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sourceSheet = ss.getActiveSheet();
  const data = sourceSheet.getDataRange().getValues();

  if (data.length === 0) {
    SpreadsheetApp.getUi().alert('The sheet is empty!');
    return;
  }

  // Row 1 is the header
  const header = data[0];

  // Find the DM columns
  const dmStartIndex = findDmStartIndex(header);

  Logger.log('DM columns start at index: ' + dmStartIndex + ' (column ' + columnToLetter(dmStartIndex) + ')');

  // ── Parse company blocks ──────────────────────────────────────────────────
  const companyBlocks = parseCompanyBlocks(data, dmStartIndex);

  Logger.log('Found ' + companyBlocks.length + ' companies');

  // ── Build clean output ────────────────────────────────────────────────────
  const cleanData = [];
  cleanData.push(header); // keep original header

  companyBlocks.forEach(function(block) {
    const companyRow = block.companyRow.slice(); // clone the main company row

    // Consolidate DM columns from all sub-rows
    for (var col = dmStartIndex; col < header.length; col++) {
      var values = [];

      // Collect from the main company row
      var mainVal = String(companyRow[col] || '').trim();
      if (mainVal !== '') {
        values.push(mainVal);
      }

      // Collect from all DM sub-rows
      block.dmRows.forEach(function(dmRow) {
        var val = String(dmRow[col] || '').trim();
        if (val !== '') {
          values.push(val);
        }
      });

      // Join with semicolons — keep blank if no values found
      var unique = removeDuplicates(values);
      companyRow[col] = unique.length > 0 ? unique.join('; ') : '';
    }

    // Keep ALL company columns as-is (including blanks for enrichment)
    cleanData.push(companyRow);
  });

  // ── Write to new sheet ────────────────────────────────────────────────────
  var cleanSheet = ss.getSheetByName('Cleaned_Data');
  if (cleanSheet) {
    cleanSheet.clear();
  } else {
    cleanSheet = ss.insertSheet('Cleaned_Data');
  }

  if (cleanData.length > 0 && cleanData[0].length > 0) {
    cleanSheet.getRange(1, 1, cleanData.length, cleanData[0].length).setValues(cleanData);

    // Format header row
    var headerRange = cleanSheet.getRange(1, 1, 1, cleanData[0].length);
    headerRange.setFontWeight('bold');
    headerRange.setBackground('#4285f4');
    headerRange.setFontColor('#ffffff');
    cleanSheet.setFrozenRows(1);

    // Auto-resize columns (first 20 to avoid very wide DM columns)
    var colsToResize = Math.min(cleanData[0].length, 20);
    for (var c = 1; c <= colsToResize; c++) {
      cleanSheet.autoResizeColumn(c);
    }
  }

  // ── Summary ───────────────────────────────────────────────────────────────
  var originalRows = data.length - 1;
  var cleanedRows = cleanData.length - 1;
  var removedRows = originalRows - cleanedRows;

  SpreadsheetApp.getUi().alert(
    'Cleaning Complete!\n\n' +
    '• Original rows: ' + originalRows + '\n' +
    '• Companies found: ' + cleanedRows + '\n' +
    '• Rows consolidated: ' + removedRows + '\n' +
    '• DM entries merged with semicolons\n' +
    '• Empty cells preserved for enrichment\n\n' +
    'Results are in the "Cleaned_Data" sheet.'
  );

  ss.setActiveSheet(cleanSheet);
}

// ─── Parse data into company blocks ─────────────────────────────────────────
function parseCompanyBlocks(data, dmStartIndex) {
  var blocks = [];
  var currentBlock = null;

  for (var i = 1; i < data.length; i++) {
    var row = data[i];

    // Skip ONLY rows that are completely empty across every single column
    if (isRowCompletelyEmpty(row)) {
      continue;
    }

    if (isCompanyRow(row, dmStartIndex)) {
      if (currentBlock) {
        blocks.push(currentBlock);
      }
      currentBlock = {
        companyRow: row,
        dmRows: []
      };
    } else {
      if (currentBlock) {
        currentBlock.dmRows.push(row);
      }
    }
  }

  if (currentBlock) {
    blocks.push(currentBlock);
  }

  return blocks;
}

// ─── Detect if a row is a company row ───────────────────────────────────────
function isCompanyRow(row, dmStartIndex) {
  var colA = String(row[0] || '').trim();
  var colB = String(row[1] || '').trim();

  if (colA.match(/^\d+\.?$/) || colB.length > 2) {
    return true;
  }

  var companyDataCount = 0;
  var checkCols = Math.min(dmStartIndex, 15);
  for (var c = 0; c < checkCols; c++) {
    if (String(row[c] || '').trim() !== '') {
      companyDataCount++;
    }
  }

  return companyDataCount >= 3;
}

// ─── Find where DM columns start ───────────────────────────────────────────
function findDmStartIndex(header) {
  for (var i = 0; i < header.length; i++) {
    var h = String(header[i] || '').toUpperCase().trim();
    if (h.indexOf('DM') > -1 || h.indexOf('DIRETT') > -1 || h.indexOf('MANAGER') > -1) {
      return i;
    }
  }

  for (var i = 18; i < header.length; i++) {
    var h = String(header[i] || '').trim();
    if (h.length > 0) {
      return i;
    }
  }

  return 20;
}

// ─── Check if a row is COMPLETELY empty (every cell blank) ──────────────────
function isRowCompletelyEmpty(row) {
  for (var i = 0; i < row.length; i++) {
    if (String(row[i] || '').trim() !== '') {
      return false;
    }
  }
  return true;
}

// ─── Remove duplicate values from array ─────────────────────────────────────
function removeDuplicates(arr) {
  var seen = {};
  var result = [];
  for (var i = 0; i < arr.length; i++) {
    var val = arr[i].trim();
    if (val !== '' && !seen[val]) {
      seen[val] = true;
      result.push(val);
    }
  }
  return result;
}

// ─── Column index to letter ─────────────────────────────────────────────────
function columnToLetter(colIndex) {
  var letter = '';
  var temp = colIndex;
  while (temp >= 0) {
    letter = String.fromCharCode((temp % 26) + 65) + letter;
    temp = Math.floor(temp / 26) - 1;
  }
  return letter;
}

// ─── Alternative: Clean in place ────────────────────────────────────────────
function cleanAidaExportInPlace() {
  var ui = SpreadsheetApp.getUi();
  var response = ui.alert(
    'Warning',
    'This will OVERWRITE your current sheet with cleaned data.\n\n' +
    'Make sure you have a backup!\n\nContinue?',
    ui.ButtonSet.YES_NO
  );

  if (response !== ui.Button.YES) return;

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getActiveSheet();
  var data = sheet.getDataRange().getValues();

  if (data.length === 0) return;

  var header = data[0];
  var dmStartIndex = findDmStartIndex(header);
  var companyBlocks = parseCompanyBlocks(data, dmStartIndex);

  var cleanData = [header];
  companyBlocks.forEach(function(block) {
    var companyRow = block.companyRow.slice();
    for (var col = dmStartIndex; col < header.length; col++) {
      var values = [];
      var mainVal = String(companyRow[col] || '').trim();
      if (mainVal !== '') values.push(mainVal);
      block.dmRows.forEach(function(dmRow) {
        var val = String(dmRow[col] || '').trim();
        if (val !== '') values.push(val);
      });
      var unique = removeDuplicates(values);
      companyRow[col] = unique.length > 0 ? unique.join('; ') : '';
    }
    cleanData.push(companyRow);
  });

  sheet.clear();
  sheet.getRange(1, 1, cleanData.length, cleanData[0].length).setValues(cleanData);

  ui.alert('Sheet cleaned in place! ' + (cleanData.length - 1) + ' companies remain.\nEmpty cells preserved for enrichment.');
}
