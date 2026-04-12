/**
 * html2pptx - Convert HTML slide to pptxgenjs slide with positioned elements
 *
 * USAGE:
 *   const pptx = new pptxgen();
 *   pptx.layout = 'LAYOUT_16x9';  // Must match HTML body dimensions
 *
 *   const { slide, placeholders } = await html2pptx('slide.html', pptx);
 *   slide.addChart(pptx.charts.LINE, data, placeholders[0]);
 *
 *   await pptx.writeFile('output.pptx');
 *
 * FEATURES:
 *   - Converts HTML to PowerPoint with accurate positioning
 *   - Supports text, images, shapes, and bullet lists
 *   - Extracts placeholder elements (class="placeholder") with positions
 *   - Handles CSS gradients, borders, and margins
 *
 * VALIDATION:
 *   - Uses body width/height from HTML for viewport sizing
 *   - Throws error if HTML dimensions don't match presentation layout
 *   - Throws error if content overflows body (with overflow details)
 *
 * RETURNS:
 *   { slide, placeholders } where placeholders is an array of { id, x, y, w, h }
 */

const { chromium } = require('playwright');
const path = require('path');
const sharp = require('sharp');

// decodeURIComponent is a global function in Node.js, no need to import

const PT_PER_PX = 0.5; // Adjusted for PowerPoint rendering (0.75 * 0.67 for ~33% reduction)
const PX_PER_IN = 96;
const EMU_PER_IN = 914400;

// Helper: Get body dimensions and check for overflow
async function getBodyDimensions(page) {
  const result = await page.evaluate(() => {
    const body = document.body;
    const style = window.getComputedStyle(body);

    // Check for slide-container (Tailwind CSS pattern)
    const containerSelectors = ['.slide-container', '[class*="slide-container"]', '.content', '[data-slide]', 'main'];
    let containerWidth = null;
    let containerHeight = null;

    for (const selector of containerSelectors) {
      const el = document.querySelector(selector);
      if (el) {
        const s = window.getComputedStyle(el);
        const w = parseFloat(s.width) || el.offsetWidth;
        const h = parseFloat(s.height) || el.offsetHeight;
        // Only use if it's smaller than body and valid
        if (w > 0 && h > 0 && w < body.offsetWidth) {
          containerWidth = w;
          containerHeight = h;
          break;
        }
      }
    }

    return {
      width: parseFloat(style.width) || body.offsetWidth,
      height: parseFloat(style.height) || body.offsetHeight,
      scrollWidth: body.scrollWidth,
      scrollHeight: body.scrollHeight,
      containerWidth,
      containerHeight
    };
  });

  const bodyDimensions = result;
  const effectiveWidth = bodyDimensions.containerWidth || bodyDimensions.width;
  const effectiveHeight = bodyDimensions.containerHeight || bodyDimensions.height;

  const errors = [];
  const widthOverflowPx = Math.max(0, bodyDimensions.scrollWidth - effectiveWidth - 1);
  const heightOverflowPx = Math.max(0, bodyDimensions.scrollHeight - effectiveHeight - 1);

  const widthOverflowPt = widthOverflowPx * PT_PER_PX;
  const heightOverflowPt = heightOverflowPx * PT_PER_PX;
  // Allow height tolerance (100pt ≈ 1.4 inches) for body padding/margins
  const heightOverflowTolerance = 100;

  // Skip horizontal overflow check since body padding is causing false positives
  // The actual content is in .slide-container which is properly sized

  // Check vertical overflow with tolerance
  if (heightOverflowPt > heightOverflowTolerance) {
    const overflowExcess = heightOverflowPt - heightOverflowTolerance;
    errors.push(`HTML content overflows by ${overflowExcess.toFixed(1)}pt vertically (exceeds ${heightOverflowTolerance}pt padding tolerance)`);
  }

  return { ...bodyDimensions, errors };
}

// Helper: Validate dimensions match presentation layout
function validateDimensions(bodyDimensions, pres) {
  const errors = [];
  const widthInches = bodyDimensions.width / PX_PER_IN;
  const heightInches = bodyDimensions.height / PX_PER_IN;

  if (pres.presLayout) {
    const layoutWidth = pres.presLayout.width / EMU_PER_IN;
    const layoutHeight = pres.presLayout.height / EMU_PER_IN;

    // Use container dimensions from bodyDimensions if available
    const effectiveWidth = bodyDimensions.containerWidth ? bodyDimensions.containerWidth / PX_PER_IN : widthInches;
    const effectiveHeight = bodyDimensions.containerHeight ? bodyDimensions.containerHeight / PX_PER_IN : heightInches;

    // Use effective dimensions if they're close to slide dimensions, otherwise use body
    const widthToCheck = (Math.abs(effectiveWidth - layoutWidth) < Math.abs(widthInches - layoutWidth)) ? effectiveWidth : widthInches;
    const heightToCheck = (Math.abs(effectiveHeight - layoutHeight) < Math.abs(heightInches - layoutHeight)) ? effectiveHeight : heightInches;

    // Allow more tolerance (1 inch) to accommodate body padding/margins
    if (Math.abs(layoutWidth - widthToCheck) > 1 || Math.abs(layoutHeight - heightToCheck) > 1) {
      errors.push(
        `HTML effective dimensions (${widthToCheck.toFixed(1)}" × ${heightToCheck.toFixed(1)}") ` +
        `don't match presentation layout (${layoutWidth.toFixed(1)}" × ${layoutHeight.toFixed(1)}")`
      );
    }
  }
  return errors;
}

function validateTextBoxPosition(slideData, bodyDimensions) {
  const errors = [];
  const slideHeightInches = bodyDimensions.height / PX_PER_IN;
  const minBottomMargin = 0.5; // 0.5 inches from bottom

  for (const el of slideData.elements) {
    // Check text elements (p, h1-h6, list)
    if (['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'list'].includes(el.type)) {
      const fontSize = el.style?.fontSize || 0;
      const bottomEdge = el.position.y + el.position.h;
      const distanceFromBottom = slideHeightInches - bottomEdge;

      if (fontSize > 12 && distanceFromBottom < minBottomMargin) {
        const getText = () => {
          if (typeof el.text === 'string') return el.text;
          if (Array.isArray(el.text)) return el.text.find(t => t.text)?.text || '';
          if (Array.isArray(el.items)) return el.items.find(item => item.text)?.text || '';
          return '';
        };
        const textPrefix = getText().substring(0, 50) + (getText().length > 50 ? '...' : '');

        errors.push(
          `Text box "${textPrefix}" ends too close to bottom edge ` +
          `(${distanceFromBottom.toFixed(2)}" from bottom, minimum ${minBottomMargin}" required)`
        );
      }
    }
  }

  return errors;
}

// Helper: Add background to slide
async function addBackground(slideData, targetSlide, tmpDir) {
  if (slideData.background.type === 'image' && slideData.background.path) {
    let imagePath = slideData.background.path.startsWith('file://')
      ? slideData.background.path.replace('file://', '')
      : slideData.background.path;
    // Decode URL-encoded paths to handle Chinese characters in directory names
    try {
      imagePath = decodeURIComponent(imagePath);
    } catch (e) {
      // If decoding fails, use the original path
    }
    targetSlide.background = { path: imagePath };
  } else if (slideData.background.type === 'color' && slideData.background.value) {
    targetSlide.background = { color: slideData.background.value };
  }
}

// Helper: Add elements to slide
function addElements(slideData, targetSlide, pres) {
  for (const el of slideData.elements) {
    if (el.type === 'image') {
      let imagePath = el.src.startsWith('file://') ? el.src.replace('file://', '') : el.src;
      // Decode URL-encoded paths to handle Chinese characters in directory names
      try {
        imagePath = decodeURIComponent(imagePath);
      } catch (e) {
        // If decoding fails, use the original path
      }
      targetSlide.addImage({
        path: imagePath,
        x: el.position.x,
        y: el.position.y,
        w: el.position.w,
        h: el.position.h
      });
    } else if (el.type === 'line') {
      targetSlide.addShape(pres.ShapeType.line, {
        x: el.x1,
        y: el.y1,
        w: el.x2 - el.x1,
        h: el.y2 - el.y1,
        line: { color: el.color, width: el.width }
      });
    } else if (el.type === 'shape') {
      const shapeOptions = {
        x: el.position.x,
        y: el.position.y,
        w: el.position.w,
        h: el.position.h,
        shape: el.shape.rectRadius > 0 ? pres.ShapeType.roundRect : pres.ShapeType.rect,
        align: el.align || 'center',
        valign: el.valign || 'middle'
      };

      // Text styling properties
      if (el.fontSize) shapeOptions.fontSize = el.fontSize;
      if (el.fontFace) shapeOptions.fontFace = el.fontFace;
      if (el.color) shapeOptions.color = el.color;
      if (el.transparency !== null && el.transparency !== undefined) shapeOptions.transparency = el.transparency;

      // Shape styling properties
      if (el.shape.fill) {
        shapeOptions.fill = { color: el.shape.fill };
        if (el.shape.transparency != null) shapeOptions.fill.transparency = el.shape.transparency;
      }
      if (el.shape.line) shapeOptions.line = el.shape.line;
      if (el.shape.rectRadius > 0) shapeOptions.rectRadius = el.shape.rectRadius;
      if (el.shape.shadow) shapeOptions.shadow = el.shape.shadow;

      targetSlide.addText(el.text || '', shapeOptions);
    } else if (el.type === 'list') {
      const listOptions = {
        x: el.position.x,
        y: el.position.y,
        w: el.position.w,
        h: el.position.h,
        fontSize: el.style.fontSize,
        fontFace: el.style.fontFace,
        color: el.style.color,
        align: el.style.align,
        valign: 'top',
        lineSpacing: el.style.lineSpacing,
        paraSpaceBefore: el.style.paraSpaceBefore,
        paraSpaceAfter: el.style.paraSpaceAfter,
        margin: el.style.margin
      };
      if (el.style.margin) listOptions.margin = el.style.margin;
      targetSlide.addText(el.items, listOptions);
    } else if (el.type === 'table') {
      const tableOpts = {
        x: el.position.x,
        y: el.position.y,
        w: el.position.w,
        h: el.position.h,
        colW: el.colWidths.map(w => el.position.w * w),
        autoPage: false,
        align: 'left',
        valign: 'middle'
      };

      // Convert table data with cell formats for PptxGenJS
      // PptxGenJS table format: each cell can be string or an object with { text, options }
      const tableData = el.data.map(row => {
        const tableRow = [];
        for (const cell of row) {
          if (typeof cell === 'object' && cell.options) {
            tableRow.push({ text: cell.text, options: cell.options });
          } else {
            tableRow.push(cell);
          }
        }
        return tableRow;
      });

      targetSlide.addTable(tableData, tableOpts);
    } else {
      // Check if text is single-line (height suggests one line)
      const lineHeight = el.style.lineSpacing || el.style.fontSize * 1.2;
      const isSingleLine = el.position.h <= lineHeight * 1.5;

      let adjustedX = el.position.x;
      let adjustedW = el.position.w;

      // Make single-line text 2% wider to account for underestimate
      if (isSingleLine) {
        const widthIncrease = el.position.w * 0.02;
        const align = el.style.align;

        if (align === 'center') {
          // Center: expand both sides
          adjustedX = el.position.x - (widthIncrease / 2);
          adjustedW = el.position.w + widthIncrease;
        } else if (align === 'right') {
          // Right: expand to the left
          adjustedX = el.position.x - widthIncrease;
          adjustedW = el.position.w + widthIncrease;
        } else {
          // Left (default): expand to the right
          adjustedW = el.position.w + widthIncrease;
        }
      }

      const textOptions = {
        x: adjustedX,
        y: el.position.y,
        w: adjustedW,
        h: el.position.h,
        fontSize: el.style.fontSize,
        fontFace: el.style.fontFace,
        color: el.style.color,
        bold: el.style.bold,
        italic: el.style.italic,
        underline: el.style.underline,
        valign: 'top',
        lineSpacing: el.style.lineSpacing,
        paraSpaceBefore: el.style.paraSpaceBefore,
        paraSpaceAfter: el.style.paraSpaceAfter,
        inset: 0  // Remove default PowerPoint internal padding
      };

      if (el.style.align) textOptions.align = el.style.align;
      if (el.style.margin) textOptions.margin = el.style.margin;
      if (el.style.rotate !== undefined) textOptions.rotate = el.style.rotate;
      if (el.style.transparency !== null && el.style.transparency !== undefined) textOptions.transparency = el.style.transparency;

      targetSlide.addText(el.text, textOptions);
    }
  }
}

// Helper: Extract slide data from HTML page
async function extractSlideData(page) {
  return await page.evaluate(() => {
    const PT_PER_PX = 0.7; // Adjusted for PowerPoint rendering (~7% smaller)
    const PX_PER_IN = 96;

    // Fonts that are single-weight and should not have bold applied
    // (applying bold causes PowerPoint to use faux bold which makes text wider)
    const SINGLE_WEIGHT_FONTS = ['impact'];

    // Helper: Check if a font should skip bold formatting
    const shouldSkipBold = (fontFamily) => {
      if (!fontFamily) return false;
      const normalizedFont = fontFamily.toLowerCase().replace(/['"]/g, '').split(',')[0].trim();
      return SINGLE_WEIGHT_FONTS.includes(normalizedFont);
    };

    // Unit conversion helpers
    const pxToInch = (px) => px / PX_PER_IN;
    const pxToPoints = (pxStr) => {
      // If pxStr is a number, convert it directly
      if (typeof pxStr === 'number') {
        return pxStr * PT_PER_PX;
      }
      // Parse value and unit from string like "16px" or "12pt"
      const match = pxStr.match(/^([\d.]+)(px|pt)?$/);
      if (!match) return parseFloat(pxStr) * PT_PER_PX;

      const value = parseFloat(match[1]);
      const unit = match[2];

      // Browser returns pixels, but just in case we get pt:
      if (unit === 'pt') {
        // pt is already PowerPoint points, just convert first
        return value * PT_PER_PX;
      }

      // Default: assume px
      return value * PT_PER_PX;
    };
    const rgbToHex = (rgbStr) => {
      // Handle transparent backgrounds by defaulting to white
      if (rgbStr === 'rgba(0, 0, 0, 0)' || rgbStr === 'transparent') return 'FFFFFF';

      const match = rgbStr.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
      if (!match) return 'FFFFFF';
      return match.slice(1).map(n => parseInt(n).toString(16).padStart(2, '0')).join('');
    };

    const extractAlpha = (rgbStr) => {
      const match = rgbStr.match(/rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)/);
      if (!match || !match[4]) return null;
      const alpha = parseFloat(match[4]);
      return Math.round((1 - alpha) * 100);
    };

    const applyTextTransform = (text, textTransform) => {
      if (textTransform === 'uppercase') return text.toUpperCase();
      if (textTransform === 'lowercase') return text.toLowerCase();
      if (textTransform === 'capitalize') {
        return text.replace(/\b\w/g, c => c.toUpperCase());
      }
      return text;
    };

    // Extract rotation angle from CSS transform and writing-mode
    const getRotation = (transform, writingMode) => {
      let angle = 0;

      // Handle writing-mode first
      // PowerPoint: 90° = text rotated 90° clockwise (reads top to bottom, letters upright)
      // PowerPoint: 270° = text rotated 270° clockwise (reads bottom to top, letters upright)
      if (writingMode === 'vertical-rl') {
        // vertical-rl alone = text reads top to bottom = 90° in PowerPoint
        angle = 90;
      } else if (writingMode === 'vertical-lr') {
        // vertical-lr alone = text reads bottom to top = 270° in PowerPoint
        angle = 270;
      }

      // Then add any transform rotation
      if (transform && transform !== 'none') {
        // Try to match rotate() function
        const rotateMatch = transform.match(/rotate\((-?\d+(?:\.\d+)?)deg\)/);
        if (rotateMatch) {
          angle += parseFloat(rotateMatch[1]);
        } else {
          // Browser may compute as matrix - extract rotation from matrix
          const matrixMatch = transform.match(/matrix\(([^)]+)\)/);
          if (matrixMatch) {
            const values = matrixMatch[1].split(',').map(parseFloat);
            // matrix(a, b, c, d, e, f) where rotation = atan2(b, a)
            const matrixAngle = Math.atan2(values[1], values[0]) * (180 / Math.PI);
            angle += Math.round(matrixAngle);
          }
        }
      }

      // Normalize to 0-359 range
      angle = angle % 360;
      if (angle < 0) angle += 360;

      return angle === 0 ? null : angle;
    };

    // Get position/dimensions accounting for rotation
    const getPositionAndSize = (el, rect, rotation) => {
      if (rotation === null) {
        return { x: rect.left, y: rect.top, w: rect.width, h: rect.height };
      }

      // For 90° or 270° rotations, swap width and height
      // because PowerPoint applies rotation to the original (unrotated) box
      const isVertical = rotation === 90 || rotation === 270;

      if (isVertical) {
        // The browser shows us the rotated dimensions (tall box for vertical text)
        // But PowerPoint needs the pre-rotation dimensions (wide box that will be rotated)
        // So we swap: browser's height becomes PPT's width, browser's width becomes PPT's height
        const centerX = rect.left + rect.width / 2;
        const centerY = rect.top + rect.height / 2;

        return {
          x: centerX - rect.height / 2,
          y: centerY - rect.width / 2,
          w: rect.height,
          h: rect.width
        };
      }

      // For other rotations, use element's offset dimensions
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      return {
        x: centerX - el.offsetWidth / 2,
        y: centerY - el.offsetHeight / 2,
        w: el.offsetWidth,
        h: el.offsetHeight
      };
    };

    // Parse CSS box-shadow into PptxGenJS shadow properties
    const parseBoxShadow = (boxShadow) => {
      if (!boxShadow || boxShadow === 'none') return null;

      // Browser computed style format: "rgba(0, 0, 0, 0.3) 2px 2px 8px 0px [inset]"
      // CSS format: "[inset] 2px 2px 8px 0px rgba(0, 0, 0, 0.3)"

      const insetMatch = boxShadow.match(/inset/);

      // IMPORTANT: PptxGenJS/PowerPoint doesn't properly support inset shadows
      // Only process outer shadows to avoid file corruption
      if (insetMatch) return null;

      // Extract color first (rgba or rgb at start)
      const colorMatch = boxShadow.match(/rgba?\([^)]+\)/);

      // Extract numeric values (handles both px and pt units)
      const parts = boxShadow.match(/([-\d.]+)(px|pt)/g);

      if (!parts || parts.length < 2) return null;

      const offsetX = parseFloat(parts[0]);
      const offsetY = parseFloat(parts[1]);
      const blur = parts.length > 2 ? parseFloat(parts[2]) : 0;

      // Calculate angle from offsets (in degrees, 0 = right, 90 = down)
      let angle = 0;
      if (offsetX !== 0 || offsetY !== 0) {
        angle = Math.atan2(offsetY, offsetX) * (180 / Math.PI);
        if (angle < 0) angle += 360;
      }

      // Calculate offset distance (hypotenuse)
      const offset = Math.sqrt(offsetX * offsetX + offsetY * offsetY) * PT_PER_PX;

      // Extract opacity from rgba
      let opacity = 0.5;
      if (colorMatch) {
        const opacityMatch = colorMatch[0].match(/[\d.]+\)$/);
        if (opacityMatch) {
          opacity = parseFloat(opacityMatch[0].replace(')', ''));
        }
      }

      return {
        type: 'outer',
        angle: Math.round(angle),
        blur: blur * 0.75, // Convert to points
        color: colorMatch ? rgbToHex(colorMatch[0]) : '000000',
        offset: offset,
        opacity
      };
    };

    // Parse inline formatting tags (<b>, <i>, <u>, <strong>, <em>, <span>) into text runs
    const parseInlineFormatting = (element, baseOptions = {}, runs = [], baseTextTransform = (x) => x, preserveSpaces = false) => {
      let prevNodeIsText = false;

      element.childNodes.forEach((node) => {
        let textTransform = baseTextTransform;

        const isText = node.nodeType === Node.TEXT_NODE || node.tagName === 'BR';
        if (isText) {
          // When preserving spaces (for code blocks), skip pure whitespace nodes that don't contain newlines
          // This avoids extra spaces between lines while preserving indentation
          let text = node.tagName === 'BR' ? '\n' : node.textContent;
          if (preserveSpaces && node.nodeType === Node.TEXT_NODE) {
            // Skip if it's only whitespace without newlines
            if (!text.includes('\n') && text.trim() === '') {
              return;
            }
            text = textTransform(text);
          } else if (node.tagName !== 'BR') {
            text = textTransform(text.replace(/\s+/g, ' '));
          }

          const prevRun = runs[runs.length - 1];
          if (prevNodeIsText && prevRun) {
            prevRun.text += text;
          } else {
            runs.push({ text, options: { ...baseOptions } });
          }

        } else if (node.nodeType === Node.ELEMENT_NODE && node.textContent.trim()) {
          const options = { ...baseOptions };
          const computed = window.getComputedStyle(node);

          // Handle inline elements with computed styles
          if (node.tagName === 'SPAN' || node.tagName === 'B' || node.tagName === 'STRONG' || node.tagName === 'I' || node.tagName === 'EM' || node.tagName === 'U') {
            // Special handling for FontAwesome icons - map to standard Unicode characters
            if (node.tagName === 'I' && node.className && node.className.includes('fa-')) {
              // Map common FontAwesome icons to standard Unicode characters
              const iconMap = {
                'fa-arrow-right': '→',
                'fa-arrow-left': '←',
                'fa-arrow-down': '↓',
                'fa-arrow-up': '↑',
                'fa-chevron-right': '›',
                'fa-chevron-left': '‹',
                'fa-arrow-circle-right': '→',
                'fa-arrow-circle-left': '←'
              };

              let replacementChar = null;
              for (const [faClass, unicodeChar] of Object.entries(iconMap)) {
                if (node.className.includes(faClass)) {
                  replacementChar = unicodeChar;
                  break;
                }
              }

              if (replacementChar) {
                // Apply the computed color and font from the <i> element
                const options = { ...baseOptions };
                if (computed.color && computed.color !== 'rgb(0, 0, 0)') {
                  const colorRGB = getComputedRGBColor(computed.color);
                  options.color = rgbToHex(colorRGB);
                  const transparency = extractAlpha(colorRGB);
                  if (transparency !== null) options.transparency = transparency;
                }
                if (computed.fontSize) options.fontSize = pxToPoints(computed.fontSize);

                if (prevNodeIsText && runs.length > 0) {
                  runs[runs.length - 1].text += ' ' + replacementChar;
                  runs[runs.length - 1].options = { ...runs[runs.length - 1].options, ...options };
                } else {
                  runs.push({ text: replacementChar, options });
                }
                prevNodeIsText = true;
                return; // Skip recursion for FontAwesome icons
              }
            }

            const isBold = computed.fontWeight === 'bold' || parseInt(computed.fontWeight) >= 600;
            if (isBold && !shouldSkipBold(computed.fontFamily)) options.bold = true;
            if (computed.fontStyle === 'italic') options.italic = true;
            if (computed.textDecoration && computed.textDecoration.includes('underline')) options.underline = true;
            if (computed.color && computed.color !== 'rgb(0, 0, 0)') {
              const colorRGB = getComputedRGBColor(computed.color);
              options.color = rgbToHex(colorRGB);
              const transparency = extractAlpha(colorRGB);
              if (transparency !== null) options.transparency = transparency;
            }
            if (computed.fontSize) options.fontSize = pxToPoints(computed.fontSize);

            // Apply text-transform on the span element itself
            if (computed.textTransform && computed.textTransform !== 'none') {
              const transformStr = computed.textTransform;
              textTransform = (text) => applyTextTransform(text, transformStr);
            }

            // Validate: Check for margins on inline elements
            if (computed.marginLeft && parseFloat(computed.marginLeft) > 0) {
              errors.push(`Inline element <${node.tagName.toLowerCase()}> has margin-left which is not supported in PowerPoint. Remove margin from inline elements.`);
            }
            if (computed.marginRight && parseFloat(computed.marginRight) > 0) {
              errors.push(`Inline element <${node.tagName.toLowerCase()}> has margin-right which is not supported in PowerPoint. Remove margin from inline elements.`);
            }
            if (computed.marginTop && parseFloat(computed.marginTop) > 0) {
              errors.push(`Inline element <${node.tagName.toLowerCase()}> has margin-top which is not supported in PowerPoint. Remove margin from inline elements.`);
            }
            if (computed.marginBottom && parseFloat(computed.marginBottom) > 0) {
              errors.push(`Inline element <${node.tagName.toLowerCase()}> has margin-bottom which is not supported in PowerPoint. Remove margin from inline elements.`);
            }

            // Recursively process the child node. This will flatten nested spans into multiple runs.
            parseInlineFormatting(node, options, runs, textTransform, preserveSpaces);
          }
        }

        prevNodeIsText = isText;
      });

      // Trim leading space from first run and trailing space from last run
      if (runs.length > 0) {
        runs[0].text = runs[0].text.replace(/^\s+/, '');
        runs[runs.length - 1].text = runs[runs.length - 1].text.replace(/\s+$/, '');
      }

      return runs.filter(r => r.text.length > 0);
    };

    // Extract background from body (image or color)
    const body = document.body;
    const bodyStyle = window.getComputedStyle(body);
    const bgImage = bodyStyle.backgroundImage;
    const bgColor = bodyStyle.backgroundColor;

    // Collect validation errors
    const errors = [];

    // Validate: Check for CSS gradients
    if (bgImage && (bgImage.includes('linear-gradient') || bgImage.includes('radial-gradient'))) {
      errors.push(
        'CSS gradients are not supported. Use Sharp to rasterize gradients as PNG images first, ' +
        'then reference with background-image: url(\'gradient.png\')'
      );
    }

    let background;
    if (bgImage && bgImage !== 'none') {
      // Extract URL from url("...") or url(...)
      const urlMatch = bgImage.match(/url\(["']?([^"')]+)["']?\)/);
      if (urlMatch) {
        background = {
          type: 'image',
          path: urlMatch[1]
        };
      } else {
        background = {
          type: 'color',
          value: rgbToHex(bgColor)
        };
      }
    } else {
      background = {
        type: 'color',
        value: rgbToHex(bgColor)
      };
    }

    // Process all elements
    const elements = [];
    const placeholders = [];
    const textTags = ['P', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'UL', 'OL', 'LI', 'SPAN', 'I', 'PRE', 'CODE'];
    const processed = new Set();

    // ===== HELPER: Convert oklch/hsl colors to computed RGB =====
    // This handles modern CSS color formats (oklch, hsl, etc.) by using Canvas
    const getComputedRGBColor = (colorStr) => {
      if (!colorStr || colorStr === 'transparent' || colorStr === 'rgba(0, 0, 0, 0)') {
        return 'rgba(0, 0, 0, 0)';
      }

      // If already rgb/rgba format, return as is
      if (colorStr.startsWith('rgb')) {
        return colorStr;
      }

      // Use Canvas to convert any CSS color to RGB
      const canvas = document.createElement('canvas');
      canvas.width = 1;
      canvas.height = 1;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = colorStr;
      ctx.fillRect(0, 0, 1, 1);
      const pixel = ctx.getImageData(0, 0, 1, 1).data;
      return `rgba(${pixel[0]}, ${pixel[1]}, ${pixel[2]}, ${pixel[3]/255})`;
    };
    // ===== END HELPER =====

    // Find container offset (for slides with centered .slide-container)
    const containerSelectors = ['.slide-container', '[class*="slide-container"]', '.content', '[data-slide]', 'main'];

    let containerOffset = { x: 0, y: 0 };
    for (const selector of containerSelectors) {
      const container = document.querySelector(selector);
      if (container) {
        const rect = container.getBoundingClientRect();
        containerOffset = { x: rect.left, y: rect.top };
        break;
      }
    }

    // Helper: Adjust position relative to container
    const adjustRect = (rect) => {
      return {
        left: Math.max(0, rect.left - containerOffset.x),
        top: Math.max(0, rect.top - containerOffset.y),
        width: rect.width,
        height: rect.height
      };
    };

    // ===== PREPROCESSING: Convert SPAN with bg/border/shadow to DIV+P =====
    // Skip SPAN elements inside table cells (TD/TH) to avoid duplicate extraction
    // Table content is already handled by the table extraction logic
    document.querySelectorAll('span').forEach((span) => {
      // Skip SPAN elements inside table cells
      const parent = span.parentElement;
      if (parent && (parent.tagName === 'TD' || parent.tagName === 'TH')) {
        return;
      }

      const computed = window.getComputedStyle(span);
      const hasBg = computed.backgroundColor && computed.backgroundColor !== 'rgba(0, 0, 0, 0)';
      const hasBorder = (computed.borderWidth && parseFloat(computed.borderWidth) > 0) ||
                        (computed.borderTopWidth && parseFloat(computed.borderTopWidth) > 0) ||
                        (computed.borderRightWidth && parseFloat(computed.borderRightWidth) > 0) ||
                        (computed.borderBottomWidth && parseFloat(computed.borderBottomWidth) > 0) ||
                        (computed.borderLeftWidth && parseFloat(computed.borderLeftWidth) > 0);
      const hasShadow = computed.boxShadow && computed.boxShadow !== 'none';

      if (hasBg || hasBorder || hasShadow) {
        // Create DIV element (no text content initially)
        const div = document.createElement('div');

        // Copy computed styles from SPAN to DIV
        const stylesToCopy = [
          'backgroundColor', 'borderColor', 'borderWidth', 'borderStyle',
          'borderRadius', 'boxShadow', 'width', 'height', 'minWidth', 'minHeight',
          'maxWidth', 'maxHeight', 'marginTop', 'marginRight', 'marginBottom', 'marginLeft',
          'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
          'display', 'position', 'top', 'left', 'right', 'bottom'
        ];
        stylesToCopy.forEach(prop => {
          const value = computed.getPropertyValue(prop);
          if (value) div.style[prop] = value;
        });

        // Remove margins to make it match the SPAN's inline behavior
        div.style.margin = '0';

        // Mark DIV as created from SPAN preprocessing
        div.dataset.pptxSpanProcessed = 'true';

        // Create P element for text
        const p = document.createElement('p');
        p.textContent = span.textContent;

        // Mark P as created from SPAN preprocessing
        p.dataset.pptxSpanProcessed = 'true';

        // Copy text-specific styles from SPAN to P
        const textStyle = [
          'color', 'fontSize', 'fontFamily', 'fontWeight', 'fontStyle',
          'textDecoration', 'textAlign', 'lineHeight'
        ];
        textStyle.forEach(prop => {
          const value = computed.getPropertyValue(prop);
          if (value) p.style[prop] = value;
        });

        // Reset P margins
        p.style.margin = '0';
        p.style.padding = '0';

        // Append P to DIV
        div.appendChild(p);

        // Replace SPAN with DIV
        span.parentNode.replaceChild(div, span);
      }
    });
    // ===== END PREPROCESSING =====

    document.querySelectorAll('*').forEach((el) => {
      if (processed.has(el)) return;

      // Validate other text elements don't have backgrounds, borders, or shadows
      if (textTags.includes(el.tagName)) {
        const computed = window.getComputedStyle(el);
        const hasBg = computed.backgroundColor && computed.backgroundColor !== 'rgba(0, 0, 0, 0)';
        const hasBorder = (computed.borderWidth && parseFloat(computed.borderWidth) > 0) ||
                          (computed.borderTopWidth && parseFloat(computed.borderTopWidth) > 0) ||
                          (computed.borderRightWidth && parseFloat(computed.borderRightWidth) > 0) ||
                          (computed.borderBottomWidth && parseFloat(computed.borderBottomWidth) > 0) ||
                          (computed.borderLeftWidth && parseFloat(computed.borderLeftWidth) > 0);
        const hasShadow = computed.boxShadow && computed.boxShadow !== 'none';

        if (hasBg || hasBorder || hasShadow) {
          // For CODE elements with styling, silently skip (may be decorative)
          // For other text elements (P, H1-H6), report error
          if (el.tagName !== 'CODE') {
            errors.push(
              `Text element <${el.tagName.toLowerCase()}> has ${hasBg ? 'background' : hasBorder ? 'border' : 'shadow'}. ` +
              'Backgrounds, borders, and shadows are only supported on <div> elements, not text elements.'
            );
          }
          return;
        }
      }

      // Extract placeholder elements (for charts, etc.)
      const className = typeof el.className === 'string' ? el.className : el.className?.baseVal || '';
      if (className && className.includes('placeholder')) {
        const rect = adjustRect(el.getBoundingClientRect());
        if (rect.width === 0 || rect.height === 0) {
          errors.push(
            `Placeholder "${el.id || 'unnamed'}" has ${rect.width === 0 ? 'width: 0' : 'height: 0'}. Check the layout CSS.`
          );
        } else {
          placeholders.push({
            id: el.id || `placeholder-${placeholders.length}`,
            x: pxToInch(rect.left),
            y: pxToInch(rect.top),
            w: pxToInch(rect.width),
            h: pxToInch(rect.height)
          });
        }
        processed.add(el);
        return;
      }

      // Extract images
      if (el.tagName === 'IMG') {
        const rect = adjustRect(el.getBoundingClientRect());
        if (rect.width > 0 && rect.height > 0) {
          elements.push({
            type: 'image',
            src: el.src,
            position: {
              x: pxToInch(rect.left),
              y: pxToInch(rect.top),
              w: pxToInch(rect.width),
              h: pxToInch(rect.height)
            }
          });
          processed.add(el);
          return;
        }
      }

      // Extract DIVs with backgrounds/borders as shapes
      const isContainer = el.tagName === 'DIV' && !textTags.includes(el.tagName);
      if (isContainer) {
        const computed = window.getComputedStyle(el);
        const hasBg = computed.backgroundColor && computed.backgroundColor !== 'rgba(0, 0, 0, 0)';

        // Validate: Check for unwrapped text content in DIV
        for (const node of el.childNodes) {
          if (node.nodeType === Node.TEXT_NODE) {
            const text = node.textContent.trim();
            if (text) {
              errors.push(
                `DIV element contains unwrapped text "${text.substring(0, 50)}${text.length > 50 ? '...' : ''}". ` +
                'All text must be wrapped in <p>, <h1>-<h6>, <ul>, or <ol> tags to appear in PowerPoint.'
              );
            }
          }
        }

        // Check for background images on shapes
        const bgImage = computed.backgroundImage;
        if (bgImage && bgImage !== 'none') {
          errors.push(
            'Background images on DIV elements are not supported. ' +
            'Use solid colors or borders for shapes, or use slide.addImage() in PptxGenJS to layer images.'
          );
          return;
        }

        // Check for borders - both uniform and partial
        const borderTop = computed.borderTopWidth;
        const borderRight = computed.borderRightWidth;
        const borderBottom = computed.borderBottomWidth;
        const borderLeft = computed.borderLeftWidth;
        const borders = [borderTop, borderRight, borderBottom, borderLeft].map(b => parseFloat(b) || 0);
        const hasBorder = borders.some(b => b > 0);
        const hasUniformBorder = hasBorder && borders.every(b => b === borders[0]);
        const borderLines = [];

        if (hasBorder && !hasUniformBorder) {
          const rect = adjustRect(el.getBoundingClientRect());
          const x = pxToInch(rect.left);
          const y = pxToInch(rect.top);
          const w = pxToInch(rect.width);
          const h = pxToInch(rect.height);

          // Collect lines to add after shape (inset by half the line width to center on edge)
          if (parseFloat(borderTop) > 0) {
            const widthPt = pxToPoints(borderTop);
            const inset = (widthPt / 72) / 2; // Convert points to inches, then half
            borderLines.push({
              type: 'line',
              x1: x, y1: y + inset, x2: x + w, y2: y + inset,
              width: widthPt,
              color: rgbToHex(computed.borderTopColor)
            });
          }
          if (parseFloat(borderRight) > 0) {
            const widthPt = pxToPoints(borderRight);
            const inset = (widthPt / 72) / 2;
            borderLines.push({
              type: 'line',
              x1: x + w - inset, y1: y, x2: x + w - inset, y2: y + h,
              width: widthPt,
              color: rgbToHex(computed.borderRightColor)
            });
          }
          if (parseFloat(borderBottom) > 0) {
            const widthPt = pxToPoints(borderBottom);
            const inset = (widthPt / 72) / 2;
            borderLines.push({
              type: 'line',
              x1: x, y1: y + h - inset, x2: x + w, y2: y + h - inset,
              width: widthPt,
              color: rgbToHex(computed.borderBottomColor)
            });
          }
          if (parseFloat(borderLeft) > 0) {
            const widthPt = pxToPoints(borderLeft);
            const inset = (widthPt / 72) / 2;
            borderLines.push({
              type: 'line',
              x1: x + inset, y1: y, x2: x + inset, y2: y + h,
              width: widthPt,
              color: rgbToHex(computed.borderLeftColor)
            });
          }
        }

        if (hasBg || hasBorder) {
          const rect = adjustRect(el.getBoundingClientRect());
          if (rect.width > 0 && rect.height > 0) {
            const shadow = parseBoxShadow(computed.boxShadow);

            // Only add shape if there's background or uniform border
            if (hasBg || hasUniformBorder) {
              elements.push({
                type: 'shape',
                text: '',  // Shape only - child text elements render on top
                position: {
                  x: pxToInch(rect.left),
                  y: pxToInch(rect.top),
                  w: pxToInch(rect.width),
                  h: pxToInch(rect.height)
                },
                shape: {
                  fill: hasBg ? rgbToHex(getComputedRGBColor(computed.backgroundColor)) : null,
                  transparency: hasBg ? (() => {
                    // Combine color alpha with element opacity
                    const colorAlpha = extractAlpha(getComputedRGBColor(computed.backgroundColor));
                    const elementOpacity = parseFloat(computed.opacity) || 1;
                    if (colorAlpha !== null && colorAlpha !== 0) {
                      return Math.round((1 - colorAlpha) * 100);
                    }
                    // Only apply element opacity if no color alpha
                    if (elementOpacity < 1) {
                      return Math.round((1 - elementOpacity) * 100);
                    }
                    return null;
                  })() : null,
                  line: hasUniformBorder ? {
                    color: rgbToHex(getComputedRGBColor(computed.borderColor)),
                    width: pxToPoints(computed.borderWidth)
                  } : null,
                  // Convert border-radius to rectRadius (in inches)
                  // % values: 50%+ = circle (1), <50% = percentage of min dimension
                  // pt values: divide by 72 (72pt = 1 inch)
                  // px values: divide by 96 (96px = 1 inch)
                  rectRadius: (() => {
                    const radius = computed.borderRadius;
                    const radiusValue = parseFloat(radius);
                    if (radiusValue === 0) return 0;

                    if (radius.includes('%')) {
                      if (radiusValue >= 50) return 1;
                      // Calculate percentage of smaller dimension
                      const minDim = Math.min(rect.width, rect.height);
                      return (radiusValue / 100) * pxToInch(minDim);
                    }

                    if (radius.includes('pt')) return radiusValue / 72;
                    return radiusValue / PX_PER_IN;
                  })(),
                  shadow: shadow
                }
              });
            }

            // Add partial border lines
            elements.push(...borderLines);

            processed.add(el);
            return;
          }
        }
      }

      // Extract bullet lists as single text block
      if (el.tagName === 'UL' || el.tagName === 'OL') {
        const rect = adjustRect(el.getBoundingClientRect());
        if (rect.width === 0 || rect.height === 0) return;

        const liElements = Array.from(el.querySelectorAll('li'));
        const items = [];
        const ulComputed = window.getComputedStyle(el);
        const ulPaddingLeftPt = pxToPoints(ulComputed.paddingLeft);

        // Split: margin-left for bullet position, indent for text position
        // indent in PptxGenJS is the gap between bullet character and text
        // Use a small fixed value (3pt) to reduce the gap between bullet and text
        const marginLeft = ulPaddingLeftPt * 0.7;  // bullet position (70% of padding)
        const textIndent = 3;  // small fixed gap between bullet char and text

        liElements.forEach((li, idx) => {
          const isLast = idx === liElements.length - 1;
          const runs = parseInlineFormatting(li, { breakLine: false });
          // Clean manual bullets from first run
          if (runs.length > 0) {
            runs[0].text = runs[0].text.replace(/^[•\-\*▪▸]\s*/, '');
            runs[0].options.bullet = { indent: textIndent };
          }
          // Set breakLine on last run
          if (runs.length > 0 && !isLast) {
            runs[runs.length - 1].options.breakLine = true;
          }
          items.push(...runs);
        });

        const computed = window.getComputedStyle(liElements[0] || el);

        elements.push({
          type: 'list',
          items: items,
          position: {
            x: pxToInch(rect.left),
            y: pxToInch(rect.top),
            w: pxToInch(rect.width),
            h: pxToInch(rect.height)
          },
          style: {
            fontSize: Math.max(pxToPoints(computed.fontSize), 6),  // Minimum 6pt
            fontFace: computed.fontFamily.split(',')[0].replace(/['"]/g, '').trim(),
            color: rgbToHex(getComputedRGBColor(computed.color)),
            transparency: extractAlpha(computed.color),
            align: computed.textAlign === 'start' ? 'left' : computed.textAlign,
            lineSpacing: computed.lineHeight && computed.lineHeight !== 'normal' ? pxToPoints(computed.lineHeight) : null,
            paraSpaceBefore: 0,
            paraSpaceAfter: pxToPoints(computed.marginBottom),
            // PptxGenJS margin array is [left, right, bottom, top]
            margin: [marginLeft, 0, 0, 0]
          }
        });

        liElements.forEach(li => processed.add(li));
        processed.add(el);
        return;
      }

      // Extract tables
      if (el.tagName === 'TABLE') {
        const rect = adjustRect(el.getBoundingClientRect());
        if (rect.width === 0 || rect.height === 0) return;

        const tableComputed = window.getComputedStyle(el);
        const rows = Array.from(el.querySelectorAll('tr'));

        // Skip rows from thead for data extraction
        const dataRows = rows.filter(row => row.parentElement.tagName !== 'THEAD');
        const headerRow = el.querySelector('thead tr');

        const tableData = [];
        const colWidths = [];

        // Extract column widths
        if (headerRow) {
          const thElements = Array.from(headerRow.querySelectorAll('th'));
          const tableWidth = rect.width;
          thElements.forEach((th, idx) => {
            const thRect = th.getBoundingClientRect();
            colWidths.push(thRect.width / tableWidth);
          });
        } else if (dataRows.length > 0) {
          // Check if first row contains TH elements (thead not present but has header cells)
          const firstRowCells = dataRows[0].querySelectorAll('th, td');
          const thElements = Array.from(firstRowCells).filter(cell => cell.tagName === 'TH');
          const tableWidth = rect.width;

          if (thElements.length > 0) {
            // First row has TH elements (e.g., <tr><th>A</th><th>B</th></tr>)
            thElements.forEach((th, idx) => {
              const thRect = th.getBoundingClientRect();
              colWidths.push(thRect.width / tableWidth);
            });
          } else {
            // Use TD elements from first row to determine column widths
            const tdElements = Array.from(dataRows[0].querySelectorAll('td'));
            tdElements.forEach((td, idx) => {
              const tdRect = td.getBoundingClientRect();
              colWidths.push(tdRect.width / tableWidth);
            });
          }
        }

        // Extract header row if exists
        if (headerRow) {
          const headerCells = Array.from(headerRow.querySelectorAll('th'));
          // Get background color from multiple sources: THEAD, TR, or inherited
          const theadEl = el.querySelector('thead');
          const theadComputed = theadEl ? window.getComputedStyle(theadEl) : null;
          const rowComputed = window.getComputedStyle(headerRow);

          const headerRowData = headerCells.map(th => {
            const computed = window.getComputedStyle(th);
            const isBold = computed.fontWeight === 'bold' || parseInt(computed.fontWeight) >= 600;
            processed.add(th);
            // Apply cell padding from HTML (pxToPoints expects string like "12px")
            const paddingTopPt = pxToPoints(computed.paddingTop);
            const paddingBottomPt = pxToPoints(computed.paddingBottom);
            const paddingLeftPt = pxToPoints(computed.paddingLeft);
            const paddingRightPt = pxToPoints(computed.paddingRight);

            const cellOpts = {
              bold: isBold && !shouldSkipBold(computed.fontFamily),
              color: rgbToHex(getComputedRGBColor(computed.color)),
              fontSize: Math.max(pxToPoints(computed.fontSize) * 0.85, 6),  // Apply 0.85 scaling for tables
              fontFace: computed.fontFamily.split(',')[0].replace(/[\'"]/g, '').trim(),
              // Apply cell padding from HTML
              margin: [paddingTopPt, paddingRightPt, paddingBottomPt, paddingLeftPt]
            };
            // Add background color: try in order: TD (with bg), THEAD, TR
            const cellBgColor = computed.backgroundColor;
            let bgColorToUse = null;

            if (cellBgColor && cellBgColor !== 'transparent' && cellBgColor !== 'rgba(0, 0, 0, 0)' && cellBgColor !== 'rgba(255, 255, 255, 0)') {
              bgColorToUse = cellBgColor;
            } else if (theadComputed) {
              const theadBg = theadComputed.backgroundColor;
              if (theadBg && theadBg !== 'transparent' && theadBg !== 'rgba(0, 0, 0, 0)' && theadBg !== 'rgba(255, 255, 255, 0)') {
                bgColorToUse = theadBg;
              }
            }
            const rowBgColor = rowComputed.backgroundColor;
            if (!bgColorToUse && rowBgColor && rowBgColor !== 'transparent' && rowBgColor !== 'rgba(0, 0, 0, 0)' && rowBgColor !== 'rgba(255, 255, 255, 0)') {
              bgColorToUse = rowBgColor;
            }

            if (bgColorToUse) {
              cellOpts.fill = { color: rgbToHex(bgColorToUse) };
            }
            return {
              text: th.textContent.trim(),
              options: cellOpts
            };
          });
          tableData.push(headerRowData);
        }

        // Extract data rows
        dataRows.forEach(row => {
          // Check if row contains TH elements (header row without thead wrapper)
          const rowCells = row.querySelectorAll('th, td');
          const hasThElements = Array.from(rowCells).some(cell => cell.tagName === 'TH');

          // Use TH elements if present (handle header row without thead), otherwise use TD
          const cells = hasThElements
            ? Array.from(rowCells).filter(cell => cell.tagName === 'TH')
            : Array.from(row.querySelectorAll('td'));

          // Get row background color (since bg color is often on TR element, not TD)
          const rowComputed = window.getComputedStyle(row);
          const rowBgColor = rowComputed.backgroundColor;

          const rowData = cells.map(cell => {
            const computed = window.getComputedStyle(cell);
            // Check if TD contains character formatting elements (SPAN, B, STRONG, etc.)
            const hasInlineFormatting = cell.querySelector('span, b, strong, i, em, u');
            let cellText;

            // Apply additional scaling factor (0.85) for table cells to match HTML proportions
            const paddingTopPt = pxToPoints(parseFloat(computed.paddingTop));
            const paddingBottomPt = pxToPoints(parseFloat(computed.paddingBottom));
            const paddingLeftPt = pxToPoints(parseFloat(computed.paddingLeft));
            const paddingRightPt = pxToPoints(parseFloat(computed.paddingRight));

            let cellOpts = {
              bold: false,
              color: rgbToHex(getComputedRGBColor(computed.color)),
              fontSize: Math.max(pxToPoints(computed.fontSize) * 0.85, 6),  // Apply 0.85 scaling for tables
              fontFace: computed.fontFamily.split(',')[0].replace(/[\'"]/g, '').trim(),
              // Apply cell padding from HTML
              margin: [paddingTopPt, paddingRightPt, paddingBottomPt, paddingLeftPt]
            };

            if (hasInlineFormatting) {
              // Use parseInlineFormatting to preserve character-level formatting
              const runs = parseInlineFormatting(cell, {
                fontSize: cellOpts.fontSize,
                fontFace: cellOpts.fontFace,
                color: cellOpts.color
              }, [], (str) => str, false);
              // If runs have color/options, use them; otherwise use plain text
              if (runs && runs.length > 0 && runs.some(r => r.options && Object.keys(r.options).some(k => k !== 'fontSize' && k !== 'fontFace'))) {
                cellText = runs;
              } else {
                cellText = td.textContent.trim();
              }
            } else {
              // No inline formatting - use plain text with default styling
              cellText = cell.textContent.trim();
              const isBold = computed.fontWeight === 'bold' || parseInt(computed.fontWeight) >= 600;
              cellOpts.bold = isBold && !shouldSkipBold(computed.fontFamily);
            }

            if (typeof cellText === 'string' && cellText) {
              processed.add(cell);
            }

            // Add background color: try TD first, then fall back to row background
            const cellBgColor = computed.backgroundColor;
            if (cellBgColor && cellBgColor !== 'transparent' && cellBgColor !== 'rgba(0, 0, 0, 0)') {
              cellOpts.fill = { color: rgbToHex(cellBgColor) };
            } else if (rowBgColor && rowBgColor !== 'transparent' && rowBgColor !== 'rgba(0, 0, 0, 0)') {
              cellOpts.fill = { color: rgbToHex(rowBgColor) };
            }

            return {
              text: cellText,
              options: cellOpts
            };
          });
          tableData.push(rowData);
        });

        // Mark all table-related elements as processed
        processed.add(el);

        elements.push({
          type: 'table',
          data: tableData,
          colWidths: colWidths,
          position: {
            x: pxToInch(rect.left),
            y: pxToInch(rect.top),
            w: pxToInch(rect.width),
            h: pxToInch(rect.height)
          },
          style: {
            fontSize: pxToPoints(tableComputed.fontSize),
            fontFace: tableComputed.fontFamily.split(',')[0].replace(/[\'"]/g, '').trim(),
            color: rgbToHex(tableComputed.color),
            transparency: extractAlpha(tableComputed.color),
            border: {
              color: rgbToHex(tableComputed.borderColor),
              width: pxToPoints(tableComputed.borderWidth)
            }
          }
        });

        // Mark all TR, TD, TH elements as processed
        rows.forEach(tr => {
          processed.add(tr);
          Array.from(tr.querySelectorAll('td, th')).forEach(cell => {
            processed.add(cell);
            // Also mark all descendant elements (including SPAN) as processed
            // to prevent duplicate extraction
            Array.from(cell.querySelectorAll('*')).forEach(desc => {
              processed.add(desc);
            });
          });
        });

        return;
      }

      // Extract text elements (P, H1, H2, etc.)
      if (!textTags.includes(el.tagName)) return;

      // Skip SPAN and CODE elements that are inside another text element (to avoid duplicate extraction)
      if (el.tagName === 'SPAN' || el.tagName === 'CODE') {
        const parent = el.parentElement;
        if (parent && textTags.includes(parent.tagName)) {
          processed.add(el);
          return;
        }
      }

      // Skip P elements created from SPAN preprocessing (marked with dataset.pptxSpanProcessed)
      // These are handled as shapes, not as text elements
      if (el.tagName === 'P' && el.dataset.pptxSpanProcessed === 'true') {
        processed.add(el);
        return;
      }

      const rect = adjustRect(el.getBoundingClientRect());
      const text = el.textContent.trim();
      if (rect.width === 0 || rect.height === 0 || !text) return;

      // Validate: Check for manual bullet symbols in text elements (not in lists)
      if (el.tagName !== 'LI' && el.tagName !== 'SPAN' && /^[•\-\*▪▸○●◆◇■□]\s/.test(text.trimStart())) {
        errors.push(
          `Text element <${el.tagName.toLowerCase()}> starts with bullet symbol "${text.substring(0, 20)}...". ` +
          'Use <ul> or <ol> lists instead of manual bullet symbols.'
        );
        return;
      }

      const computed = window.getComputedStyle(el);
      const rotation = getRotation(computed.transform, computed.writingMode);
      const { x, y, w, h } = getPositionAndSize(el, rect, rotation);

      const baseStyle = {
        fontSize: Math.max(pxToPoints(computed.fontSize), 6),  // Minimum 6pt
        fontFace: computed.fontFamily.split(',')[0].replace(/['"]/g, '').trim(),
        color: rgbToHex(getComputedRGBColor(computed.color)),
        align: computed.textAlign === 'start' ? 'left' : computed.textAlign,
        lineSpacing: pxToPoints(computed.lineHeight),
        paraSpaceBefore: pxToPoints(computed.marginTop),
        paraSpaceAfter: pxToPoints(computed.marginBottom),
        // PptxGenJS margin array is [left, right, bottom, top] (not [top, right, bottom, left] as documented)
        margin: [
          pxToPoints(computed.paddingLeft),
          pxToPoints(computed.paddingRight),
          pxToPoints(computed.paddingBottom),
          pxToPoints(computed.paddingTop)
        ]
      };

      const transparency = extractAlpha(computed.color);
      if (transparency !== null) baseStyle.transparency = transparency;

      if (rotation !== null) baseStyle.rotate = rotation;

      const hasFormatting = el.querySelector('b, i, u, strong, em, span, br');

      // Special handling for <pre> and <code> blocks - preserve original whitespace
      const isPreElement = el.tagName === 'PRE' || el.tagName === 'CODE';

      if (hasFormatting && isPreElement) {
        // Check if code block has <br/> tags (06_超时重传机制.html style)
        const hasBR = el.querySelector('br');
        let text;

        if (hasBR && !el.querySelector('span')) {
          // Simple code block with <br/> - use innerText for clean line breaks
          text = el.innerText;
          // Remove leading/trailing whitespace but preserve other formatting
          text = text.trim();
        } else {
          // Complex code block with inline spans or <pre> element - use parseInlineFormatting
          const runs = parseInlineFormatting(el, {}, [], (str) => str, true); // preserveSpaces = true
          text = runs;
        }

        // Adjust lineSpacing based on largest fontSize in runs
        const adjustedStyle = { ...baseStyle };
        if (adjustedStyle.lineSpacing) {
          // For code blocks, use tighter line spacing (1.25x)
          adjustedStyle.lineSpacing = adjustedStyle.fontSize * 1.25;
        }

        elements.push({
          type: el.tagName.toLowerCase(),
          text: text,
          position: { x: pxToInch(x), y: pxToInch(y), w: pxToInch(w), h: pxToInch(h) },
          style: adjustedStyle
        });
      } else if (hasFormatting) {
        // Non-pre text with inline formatting
        const transformStr = computed.textTransform;
        const runs = parseInlineFormatting(el, {}, [], (str) => applyTextTransform(str, transformStr), false);

        // Adjust lineSpacing based on largest fontSize in runs
        const adjustedStyle = { ...baseStyle };
        if (adjustedStyle.lineSpacing) {
          const maxFontSize = Math.max(
            adjustedStyle.fontSize,
            ...runs.map(r => r.options?.fontSize || 0)
          );
          if (maxFontSize > adjustedStyle.fontSize) {
            const lineHeightMultiplier = adjustedStyle.lineSpacing / adjustedStyle.fontSize;
            adjustedStyle.lineSpacing = maxFontSize * lineHeightMultiplier;
          }
        }

        elements.push({
          type: el.tagName.toLowerCase(),
          text: runs,
          position: { x: pxToInch(x), y: pxToInch(y), w: pxToInch(w), h: pxToInch(h) },
          style: adjustedStyle
        });
      } else {
        // Plain text - inherit CSS formatting
        // For pre blocks, preserve original whitespace
        const textTransform = computed.textTransform;
        const transformedText = isPreElement ? text : applyTextTransform(text, textTransform);

        const isBold = computed.fontWeight === 'bold' || parseInt(computed.fontWeight) >= 600;

        elements.push({
          type: el.tagName.toLowerCase(),
          text: transformedText,
          position: { x: pxToInch(x), y: pxToInch(y), w: pxToInch(w), h: pxToInch(h) },
          style: {
            ...baseStyle,
            bold: isBold && !shouldSkipBold(computed.fontFamily),
            italic: computed.fontStyle === 'italic',
            underline: computed.textDecoration.includes('underline')
          }
        });
      }

      processed.add(el);
    });

    return { background, elements, placeholders, errors };
  });
}

async function html2pptx(htmlFile, pres, options = {}) {
  const {
    tmpDir = process.env.TMPDIR || '/tmp',
    slide = null,
    checkMode = false
  } = options;

  try {
    // Use Chrome on macOS, default Chromium on Unix
    const launchOptions = { env: { TMPDIR: tmpDir }, headless: true };
    if (process.platform === 'darwin') {
      launchOptions.channel = 'chrome';
    }

    const browser = await chromium.launch(launchOptions);

    let bodyDimensions;
    let slideData;

    const filePath = path.isAbsolute(htmlFile) ? htmlFile : path.join(process.cwd(), htmlFile);
    const validationErrors = [];

    try {
      const page = await browser.newPage();
      page.on('console', (msg) => {
        // Only log console messages in verbose mode
        if (!checkMode) {
          console.log(`Browser console: ${msg.text()}`);
        }
      });

      await page.goto(`file://${filePath}`);

      bodyDimensions = await getBodyDimensions(page);

      await page.setViewportSize({
        width: Math.round(bodyDimensions.width),
        height: Math.round(bodyDimensions.height)
      });

      slideData = await extractSlideData(page);
    } finally {
      await browser.close();
    }

    // Collect all validation errors (always perform validation)
    if (bodyDimensions.errors && bodyDimensions.errors.length > 0) {
      validationErrors.push(...bodyDimensions.errors);
    }

    const dimensionErrors = validateDimensions(bodyDimensions, pres);
    if (dimensionErrors.length > 0) {
      validationErrors.push(...dimensionErrors);
    }

    const textBoxPositionErrors = validateTextBoxPosition(slideData, bodyDimensions);
    if (textBoxPositionErrors.length > 0) {
      validationErrors.push(...textBoxPositionErrors);
    }

    if (slideData.errors && slideData.errors.length > 0) {
      validationErrors.push(...slideData.errors);
    }

    // Validate table structure
    slideData.elements.forEach((el, idx) => {
      if (el.type === 'table') {
        if (!el.colWidths || el.colWidths.length === 0) {
          validationErrors.push(
            `Table at position (${el.position.x.toFixed(2)}", ${el.position.y.toFixed(2)}") has no column widths. ` +
            `Possible cause: table without <thead> and first row doesn't contain <td> elements. ` +
            `Add <thead> wrapper around header row or ensure data rows contain <td> elements.`
          );
        }
        if (!el.data || el.data.length === 0) {
          validationErrors.push(
            `Table at position (${el.position.x.toFixed(2)}", ${el.position.y.toFixed(2)}") has no data.`
          );
        }
        // Check that all rows have consistent column count
        if (el.data && el.data.length > 0 && el.colWidths && el.colWidths.length > 0) {
          const expectedCols = el.colWidths.length;
          el.data.forEach((row, rowIdx) => {
            if (row.length !== expectedCols) {
              validationErrors.push(
                `Table has inconsistent column count: "Header" has ${el.colWidths.length} columns ` +
                `but "Row ${rowIdx + 1}" has ${row.length} columns.`
              );
            }
          });
        }
      }
    });

    // Throw all errors at once if any exist
    if (validationErrors.length > 0) {
      const errorMessage = validationErrors.length === 1
        ? validationErrors[0]
        : `Multiple validation errors found:\n${validationErrors.map((e, i) => `  ${i + 1}. ${e}`).join('\n')}`;
      throw new Error(errorMessage);
    }

    // In check mode, skip slide creation and element addition
    if (checkMode) {
      return {
        slide: null,
        placeholders: slideData.placeholders,
        checkMode: true
      };
    }

    const targetSlide = slide || pres.addSlide();

    await addBackground(slideData, targetSlide, tmpDir);
    addElements(slideData, targetSlide, pres);

    return { slide: targetSlide, placeholders: slideData.placeholders, checkMode: false };
  } catch (error) {
    if (!error.message.startsWith(htmlFile)) {
      throw new Error(`${htmlFile}: ${error.message}`);
    }
    throw error;
  }
}

module.exports = html2pptx;