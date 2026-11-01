import { CanvasRenderingTarget2D, size } from 'fancy-canvas';
import { expect, it, vi } from 'vitest';
import type { Coordinate } from 'lightweight-charts';
import CrispPriceLine from './CrispPriceLine';

// A stroke or fractional rectangle here would reintroduce partially blended
// dash endpoints. These cases inspect the actual bitmap drawing operations.
function bitmap(width = 10, height = 12, ratio = 1) {
  const fills: Array<{rect: number[]; color: string; alpha: number; composite: string}> = [];
  const state = { fillStyle: 'old', globalAlpha: 0.4, globalCompositeOperation: 'multiply' };
  const stack: typeof state[] = [];
  const context = {
    ...state,
    save() { stack.push({fillStyle: this.fillStyle, globalAlpha: this.globalAlpha, globalCompositeOperation: this.globalCompositeOperation}); },
    restore() { Object.assign(this, stack.pop()); },
    setTransform: vi.fn(),
    fillRect(x: number, y: number, w: number, h: number) { fills.push({rect: [x,y,w,h], color: this.fillStyle, alpha: this.globalAlpha, composite: this.globalCompositeOperation}); },
  };
  const target = new CanvasRenderingTarget2D(context as unknown as CanvasRenderingContext2D,
    size({width: width / ratio, height: height / ratio}), size({width, height}));
  return {fills, context, target};
}
function attached(line: CrispPriceLine, y: number | null = 3.2) {
  const coordinate = vi.fn(() => y as Coordinate | null);
  const requestUpdate = vi.fn();
  line.attached({series: {priceToCoordinate: coordinate}, requestUpdate});
  return {coordinate, requestUpdate};
}

it('draws opaque integer dashes through the final partial segment and restores canvas state', () => {
  const line = new CrispPriceLine(); attached(line); line.setPrice(65210.4, '#FF6B7A');
  const image = bitmap(9);
  line.draw(image.target);
  expect(image.fills).toEqual([
    {rect: [0,3,2,1], color:'#FF6B7A', alpha:1, composite:'source-over'},
    {rect: [4,3,2,1], color:'#FF6B7A', alpha:1, composite:'source-over'},
    {rect: [8,3,1,1], color:'#FF6B7A', alpha:1, composite:'source-over'},
  ]);
  expect(image.context.globalAlpha).toBe(0.4);
  expect(image.context.fillStyle).toBe('old');
  expect(image.context.globalCompositeOperation).toBe('multiply');
});

it('uses current price coordinates on every draw after zoom, pan or resize', () => {
  const line = new CrispPriceLine(); const {coordinate} = attached(line);
  line.setPrice(11, '#FF6B7A');
  const before = bitmap(); line.draw(before.target);
  coordinate.mockReturnValue(7.6 as Coordinate);
  const after = bitmap(5); line.draw(after.target);
  expect(before.fills[0].rect).toEqual([0,3,2,1]);
  expect(after.fills.map(x=>x.rect)).toEqual([[0,8,2,1],[4,8,1,1]]);
  expect(coordinate).toHaveBeenLastCalledWith(11);
});

it('scales the one-CSS-pixel line and two-on/two-off dashes to the integer bitmap at DPR2', () => {
  const line = new CrispPriceLine(); attached(line); line.setPrice(11, '#76A1FF');
  const image = bitmap(18,24,2); line.draw(image.target);
  expect(image.fills.map(x=>x.rect)).toEqual([[0,5,4,2],[8,5,4,2],[16,5,2,2]]);
  expect(image.fills.every(x=>x.color==='#76A1FF' && x.alpha===1)).toBe(true);
});

it('keeps fractional-DPR geometry integral and clips the line at the bitmap boundary', () => {
  const line = new CrispPriceLine(); attached(line, 7.6); line.setPrice(11, '#FF6B7A');
  const image = bitmap(9,12,1.5); line.draw(image.target);
  expect(image.fills.map(x=>x.rect)).toEqual([[0,11,2,1],[4,11,2,1],[8,11,1,1]]);
});

it('requests redraws for price, direction and empty changes without retaining detached chart references', () => {
  const line = new CrispPriceLine(); const {requestUpdate, coordinate} = attached(line);
  requestUpdate.mockClear();
  line.setPrice(11, '#FF6B7A'); line.setPrice(11, '#FF6B7A');
  line.setPrice(11, '#76A1FF'); line.setPrice(null, '#76A1FF');
  expect(requestUpdate).toHaveBeenCalledTimes(3);
  const empty = bitmap(); line.draw(empty.target); expect(empty.fills).toEqual([]);
  line.setPrice(12, '#FF6B7A'); requestUpdate.mockClear(); coordinate.mockClear();
  line.detached(); line.setPrice(13, '#FF6B7A');
  line.draw(empty.target);
  expect(requestUpdate).not.toHaveBeenCalled(); expect(coordinate).not.toHaveBeenCalled();
  const next = attached(line, 2); const reattached = bitmap(); line.draw(reattached.target);
  expect(next.coordinate).toHaveBeenCalledWith(13);
  expect(reattached.fills[0].rect).toEqual([0,2,2,1]);
});

it.each([null, -5, 100, Number.NaN])('does not draw a fabricated line for unavailable/offscreen coordinate %s', y => {
  const line = new CrispPriceLine(); attached(line, y); line.setPrice(11, '#FF6B7A');
  const image = bitmap(); line.draw(image.target); expect(image.fills).toEqual([]);
});
