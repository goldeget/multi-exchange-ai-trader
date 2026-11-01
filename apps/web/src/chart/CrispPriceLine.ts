import type { IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesPrimitive } from 'lightweight-charts';

type PriceLineAttachment = {
  series: {priceToCoordinate(price: number): number | null};
  requestUpdate: () => void;
};
type PriceColor = '#FF6B7A' | '#76A1FF';

/** Visible current-price dashes without Canvas stroke endpoint antialiasing. */
export default class CrispPriceLine implements ISeriesPrimitive, IPrimitivePaneView, IPrimitivePaneRenderer {
  private series: PriceLineAttachment['series'] | null = null;
  private requestUpdate: (() => void) | null = null;
  private price: number | null = null;
  private color: PriceColor = '#FF6B7A';
  private readonly views: readonly IPrimitivePaneView[] = [this];

  attached({series, requestUpdate}: PriceLineAttachment) {
    this.series = series;
    this.requestUpdate = requestUpdate;
    requestUpdate();
  }

  detached() {
    this.series = null;
    this.requestUpdate = null;
  }

  setPrice(price: number | null, color: PriceColor) {
    const next = price !== null && Number.isFinite(price) ? price : null;
    if (next === this.price && color === this.color) return;
    this.price = next;
    this.color = color;
    this.requestUpdate?.();
  }

  paneViews() { return this.views; }
  renderer() { return this; }
  zOrder() { return 'top' as const; }

  draw(target: Parameters<IPrimitivePaneRenderer['draw']>[0]) {
    if (!this.series || this.price === null) return;
    // Read the live scale each paint; zoom, pan and resize must not reuse a y.
    const coordinate = this.series.priceToCoordinate(this.price);
    if (coordinate === null || !Number.isFinite(coordinate)) return;
    target.useBitmapCoordinateSpace(({context, bitmapSize, horizontalPixelRatio, verticalPixelRatio}) => {
      const y = Math.round(coordinate * verticalPixelRatio);
      if (y < 0 || y >= bitmapSize.height) return;
      // Match the library's one-CSS-pixel line quantization, including DPR.
      const width = Math.max(1, Math.floor(horizontalPixelRatio));
      const top = Math.max(0, y - Math.floor(width / 2));
      const bottom = Math.min(bitmapSize.height, y + Math.ceil(width / 2));
      const dash = 2 * width;
      context.globalAlpha = 1;
      context.globalCompositeOperation = 'source-over';
      context.fillStyle = this.color;
      for (let x = 0; x < bitmapSize.width; x += dash * 2) {
        context.fillRect(x, top, Math.min(dash, bitmapSize.width - x), bottom - top);
      }
    });
  }
}
