using System;
using System.IO;
using Path = System.IO.Path;
using ESRI.ArcGIS.Carto;
using ESRI.ArcGIS.Display;
using ESRI.ArcGIS.Geodatabase;
using ESRI.ArcGIS.Geometry;
using Newtonsoft.Json.Linq;

namespace ArcMapHarness.ArcMapBridgeExternal
{
    internal static class Mxd
    {
        private static string LocalPath(string value)
        {
            if (string.IsNullOrWhiteSpace(value) || !Path.IsPathRooted(value) || value.StartsWith(@"\\"))
                throw new ArgumentException("Use a local file path.");
            return Path.GetFullPath(value);
        }

        private static JArray Extent(IEnvelope box)
        {
            return box == null || box.IsEmpty ? null : new JArray(box.XMin, box.YMin, box.XMax, box.YMax);
        }

        private static string Wkt(ISpatialReference reference)
        {
            if (reference == null || reference is IUnknownCoordinateSystem)
                return null;
            string wkt;
            int length;
            ((IESRISpatialReferenceGEN)reference).ExportToESRISpatialReference(out wkt, out length);
            return wkt;
        }

        private static string Hex(IColor color)
        {
            if (color == null || color.NullColor)
                return null;
            int rgb = color.RGB;
            return String.Format("#{0:X2}{1:X2}{2:X2}", rgb & 255, rgb >> 8 & 255, rgb >> 16 & 255);
        }

        private static string LineStyle(ISimpleLineSymbol line)
        {
            if (line == null) return "solid";
            switch (line.Style)
            {
                case esriSimpleLineStyle.esriSLSDot: return "dot";
                case esriSimpleLineStyle.esriSLSDash:
                case esriSimpleLineStyle.esriSLSDashDot:
                case esriSimpleLineStyle.esriSLSDashDotDot: return "dash";
                default: return "solid";
            }
        }

        private static JObject Appearance(ILayer layer)
        {
            var feature = layer as IGeoFeatureLayer;
            if (feature == null) return null;
            var single = feature.Renderer as ISimpleRenderer;
            if (single != null) return Appearance(single.Symbol);
            var unique = feature.Renderer as IUniqueValueRenderer;
            if (unique == null || unique.FieldCount != 1 || unique.ValueCount > 120) return null;
            var other = Appearance(unique.DefaultSymbol);
            var classes = new JArray();
            JObject basis = null;
            for (int i = 0; i < unique.ValueCount; i++)
            {
                var value = unique.get_Value(i);
                var appearance = Appearance(unique.get_Symbol(value));
                if (appearance == null) continue;
                if (basis == null) basis = appearance;
                classes.Add(new JObject {
                    ["value"] = value == "<Null>" ? null : value, ["label"] = unique.get_Label(value),
                    ["color"] = appearance["color"], ["fillColor"] = appearance["fill"]
                });
            }
            if (basis == null) return other;
            basis["classification"] = new JObject { ["field"] = unique.get_Field(0), ["classes"] = classes,
                ["other"] = (string)(other?["color"]) ?? "#7d8999", ["otherFill"] = other?["fill"] };
            return basis;
        }

        private static JObject Appearance(ISymbol symbol)
        {
            var marker = symbol as ISimpleMarkerSymbol;
            if (marker != null)
            {
                string shape = "circle";
                switch (marker.Style)
                {
                    case esriSimpleMarkerStyle.esriSMSSquare: shape = "square"; break;
                    case esriSimpleMarkerStyle.esriSMSDiamond: shape = "diamond"; break;
                    case esriSimpleMarkerStyle.esriSMSCross:
                    case esriSimpleMarkerStyle.esriSMSX: shape = "cross"; break;
                }
                return new JObject { ["color"] = Hex(marker.Color), ["shape"] = shape, ["size"] = marker.Size / 0.75 };
            }
            var fill = symbol as ISimpleFillSymbol;
            var line = fill == null ? symbol as ILineSymbol : fill.Outline;
            if (line == null) return null;
            return new JObject { ["color"] = Hex(line.Color), ["pattern"] = LineStyle(line as ISimpleLineSymbol),
                ["width"] = line.Width / 0.75, ["noOutline"] = line is ISimpleLineSymbol edge &&
                    edge.Style == esriSimpleLineStyle.esriSLSNull, ["fill"] = fill == null || fill.Style == esriSimpleFillStyle.esriSFSHollow ||
                    fill.Style == esriSimpleFillStyle.esriSFSNull ? null : Hex(fill.Color) };
        }

        private const string PolygonAppearanceGroup = "Guizhi.Mxd.PolygonAppearance/1";

        private static IMapFrame Frame(IMapDocument document, IMap map)
        {
            return ((IGraphicsContainer)document.PageLayout).FindFrame(map) as IMapFrame;
        }

        private static string Background(IMapDocument document, IMap map)
        {
            var frame = Frame(document, map);
            var background = (frame?.Background ?? ((IMapAdmin2)map).NonFrameBackground) as ISymbolBackground;
            return Hex(background?.FillSymbol?.Color);
        }

        private static void SetBackground(IMapDocument document, IMap map, string color)
        {
            if (color == null) return;
            ISymbolBackground background = new SymbolBackgroundClass();
            background.FillSymbol = new SimpleFillSymbolClass { Color = Color(color),
                Outline = (ILineSymbol)new SimpleLineSymbolClass { Style = esriSimpleLineStyle.esriSLSNull } };
            var frame = Frame(document, map);
            if (frame != null) frame.Background = (IBackground)background;
            ((IMapAdmin2)map).NonFrameBackground = (IBackground)background;
        }

        private static JObject PolygonAppearance(ICompositeLayer group)
        {
            if (group.Count != 2) throw new InvalidOperationException("规智面符号图层缺少填充或边线。");
            var stroke = Layer(group.get_Layer(0));
            var fill = Layer(group.get_Layer(1));
            if ((string)stroke["name"] != "边线" || (string)fill["name"] != "填充")
                throw new InvalidOperationException("规智面符号的子图层无法识别。");
            if (!JToken.DeepEquals(stroke["workspace"], fill["workspace"]) ||
                !JToken.DeepEquals(stroke["dataset"], fill["dataset"]))
                throw new InvalidOperationException("规智面符号的边线和填充引用了不同的数据。");
            var style = (JObject)stroke["style"];
            var fillStyle = (JObject)fill["style"];
            style["fill"] = fillStyle["fill"];
            style["fillOpacity"] = fillStyle["opacity"];
            if (style["classification"] is JObject classification && fillStyle["classification"] is JObject fillClassification)
            {
                var entries = (JArray)classification["classes"];
                var fillEntries = (JArray)fillClassification["classes"];
                if (entries.Count != fillEntries.Count)
                    throw new InvalidOperationException("规智面符号的边线和填充分类不一致。");
                for (int i = 0; i < entries.Count; i++)
                {
                    if (!JToken.DeepEquals(entries[i]["value"], fillEntries[i]["value"]))
                        throw new InvalidOperationException("规智面符号的边线和填充分类不一致。");
                    entries[i]["fillColor"] = fillEntries[i]["fillColor"];
                }
                classification["otherFill"] = fillClassification["otherFill"];
            }
            stroke["valid"] = (bool)stroke["valid"] && (bool)fill["valid"];
            return stroke;
        }

        private static JObject Layer(ILayer layer)
        {
            var result = new JObject { ["name"] = layer.Name, ["visible"] = layer.Visible };
            var wrapper = layer as IBasemapSubLayer;
            if (wrapper != null && wrapper.Layer != null)
            {
                var child = Layer(wrapper.Layer);
                child["name"] = layer.Name;
                child["visible"] = layer.Visible;
                return child;
            }
            var group = layer as IGroupLayer;
            if (group != null)
            {
                var layers = (ICompositeLayer)group;
                if (((ILayerGeneralProperties)group).LayerDescription == PolygonAppearanceGroup)
                {
                    var appearance = PolygonAppearance(layers);
                    appearance["name"] = layer.Name;
                    appearance["visible"] = layer.Visible;
                    appearance["style"]["opacity"] = Opacity(layer);
                    return appearance;
                }
                var children = new JArray();
                for (int i = 0; i < layers.Count; i++)
                    children.Add(Layer(layers.get_Layer(i)));
                result["layers"] = children;
                return result;
            }
            var feature = layer as IFeatureLayer;
            var raster = layer as IRasterLayer;
            if (feature != null)
            {
                result["kind"] = "vector";
                var data = layer as IDataLayer;
                var name = data == null ? null : data.DataSourceName as IDatasetName;
                if (name != null && name.WorkspaceName != null)
                {
                    result["workspace"] = name.WorkspaceName.PathName;
                    result["dataset"] = name.Name;
                }
            }
            else if (raster != null)
            {
                result["kind"] = "raster";
                if (layer.Valid) result["source"] = raster.FilePath;
            }
            else result["kind"] = "unsupported";
            result["valid"] = layer.Valid;
            if (layer.Valid && feature != null && Appearance(layer) is JObject style)
            {
                style["opacity"] = Opacity(layer);
                style["fillOpacity"] = 100;
                result["style"] = style;
            }
            if (raster != null) result["opacity"] = Opacity(layer);
            return result;
        }

        internal static JObject Read(JObject request)
        {
            string source = LocalPath((string)request["source"]);
            if (!File.Exists(source) || !source.EndsWith(".mxd", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("Select an MXD file.");
            IMapDocument document = new MapDocumentClass();
            if (!document.get_IsMapDocument(source) || document.get_IsPasswordProtected(source))
                throw new ArgumentException("MXD cannot be read by the installed ArcGIS Desktop.");
            document.Open(source, "");
            try
            {
                var frames = new JArray();
                for (int i = 0; i < document.MapCount; i++)
                {
                    var map = document.get_Map(i);
                    var layers = new JArray();
                    for (int index = 0; index < map.LayerCount; index++)
                        layers.Add(Layer(map.get_Layer(index)));
                    frames.Add(new JObject { ["name"] = map.Name, ["crs"] = Wkt(map.SpatialReference),
                        ["bounds"] = Extent(((IActiveView)map).Extent), ["background"] = Background(document, map), ["layers"] = layers });
                }
                bool known = false;
                int major = 0, minor = 0, patch = 0, build = 0;
                document.GetVersionInfo(ref known, ref major, ref minor, ref patch, ref build);
                return new JObject { ["schema"] = "guizhi.mxd/1",
                    ["version"] = known ? String.Format("{0}.{1}.{2}.{3}", major, minor, patch, build) : null, ["frames"] = frames };
            }
            finally { document.Close(); }
        }

        private static IRgbColor Color(string value)
        {
            if (value == null || value.Length != 7 || value[0] != '#')
                throw new ArgumentException("Use a hexadecimal RGB color.");
            int rgb = Convert.ToInt32(value.Substring(1), 16);
            return new RgbColorClass { Red = rgb >> 16, Green = rgb >> 8 & 255, Blue = rgb & 255 };
        }

        private static ISymbol MakeSymbol(esriGeometryType geometry, JObject style)
        {
            var color = Color((string)style["color"]);
            double width = (double?)style["width"] ?? 1.5;
            ISimpleLineSymbol line = new SimpleLineSymbolClass { Color = color, Width = width * 0.75 };
            if ((bool?)style["noOutline"] == true) line.Style = esriSimpleLineStyle.esriSLSNull;
            if ((bool?)style["noOutline"] != true)
            {
                switch ((string)style["pattern"])
                {
                    case "dash": line.Style = esriSimpleLineStyle.esriSLSDash; break;
                    case "dot": line.Style = esriSimpleLineStyle.esriSLSDot; break;
                }
            }
            ISymbol symbol = (ISymbol)line;
            switch (geometry)
            {
                case esriGeometryType.esriGeometryPoint:
                case esriGeometryType.esriGeometryMultipoint:
                    ISimpleMarkerSymbol marker = new SimpleMarkerSymbolClass
                    { Color = color, Size = ((double?)style["size"] ?? 8) * 0.75, Outline = false };
                    switch ((string)style["shape"])
                    {
                        case "square": marker.Style = esriSimpleMarkerStyle.esriSMSSquare; break;
                        case "diamond": marker.Style = esriSimpleMarkerStyle.esriSMSDiamond; break;
                        case "cross": marker.Style = esriSimpleMarkerStyle.esriSMSCross; break;
                    }
                    symbol = (ISymbol)marker;
                    break;
                case esriGeometryType.esriGeometryPolygon:
                    var fill = (string)style["fill"];
                    ISimpleFillSymbol area = new SimpleFillSymbolClass { Outline = (ILineSymbol)line,
                        Style = fill == null ? esriSimpleFillStyle.esriSFSHollow : esriSimpleFillStyle.esriSFSSolid };
                    if (fill != null) area.Color = Color(fill);
                    symbol = (ISymbol)area;
                    break;
            }
            return symbol;
        }

        private static ISymbol ClassSymbol(esriGeometryType geometry, JObject basis, string color, JToken fill = null)
        {
            var style = (JObject)basis.DeepClone();
            style["color"] = color;
            if (style["fill"]?.Type == JTokenType.String) style["fill"] = fill ?? new JValue(color);
            return MakeSymbol(geometry, style);
        }

        private static void SetStyle(ILayer layer, JObject style)
        {
            var feature = layer as IGeoFeatureLayer;
            if (feature == null || style == null) return;
            var geometry = feature.FeatureClass.ShapeType;
            var classification = style["classification"] as JObject;
            if (classification == null)
            {
                feature.Renderer = (IFeatureRenderer)new SimpleRendererClass { Symbol = MakeSymbol(geometry, style) };
                return;
            }
            var name = (string)classification["field"];
            int index = feature.FeatureClass.FindField(name);
            if (index < 0) throw new ArgumentException("找不到符号化字段 " + name);
            IUniqueValueRenderer renderer = new UniqueValueRendererClass();
            renderer.FieldCount = 1;
            renderer.set_Field(0, name);
            renderer.set_FieldType(0, feature.FeatureClass.Fields.get_Field(index).Type == esriFieldType.esriFieldTypeString);
            renderer.DefaultSymbol = ClassSymbol(geometry, style, (string)classification["other"], classification["otherFill"]);
            renderer.DefaultLabel = "其他值";
            renderer.UseDefaultSymbol = true;
            foreach (JObject entry in (JArray)classification["classes"])
            {
                var value = (string)entry["value"] ?? "<Null>";
                renderer.AddValue(value, "", ClassSymbol(geometry, style, (string)entry["color"], entry["fillColor"]));
                renderer.set_Label(value, (string)entry["label"] ?? (value == "<Null>" ? "空值" : value));
            }
            feature.Renderer = (IFeatureRenderer)renderer;
        }

        private static int Opacity(ILayer layer)
        {
            return layer is ILayerEffects effects ? 100 - effects.Transparency : 100;
        }

        private static void SetOpacity(ILayer layer, int value)
        {
            if (value < 0 || value > 100) throw new ArgumentOutOfRangeException("opacity");
            if (layer is ILayerEffects effects) effects.Transparency = (short)(100 - value);
        }

        private static ILayer BuildLayer(JObject item)
        {
            var layer = Program.OpenLayer(item);
            var style = item["style"] as JObject;
            int opacity = (int?)style?["opacity"] ?? (int?)item["opacity"] ?? 100;
            if (style != null && layer is IFeatureLayer feature && feature.FeatureClass.ShapeType == esriGeometryType.esriGeometryPolygon &&
                style["fill"]?.Type == JTokenType.String && (int?)style["fillOpacity"] != 100)
            {
                var fill = Program.OpenLayer(item);
                fill.Name = "填充";
                layer.Name = "边线";
                var fillStyle = (JObject)style.DeepClone();
                fillStyle["noOutline"] = true;
                SetStyle(fill, fillStyle);
                SetOpacity(fill, (int?)style["fillOpacity"] ?? 19);
                var strokeStyle = (JObject)style.DeepClone();
                strokeStyle["fill"] = null;
                SetStyle(layer, strokeStyle);
                layer.Visible = fill.Visible = true;
                IGroupLayer group = new GroupLayerClass();
                var combined = (ILayer)group;
                combined.Name = (string)item["name"];
                ((ILayerGeneralProperties)group).LayerDescription = PolygonAppearanceGroup;
                group.Add(layer);
                group.Add(fill);
                SetOpacity(combined, opacity);
                return combined;
            }
            SetStyle(layer, style);
            SetOpacity(layer, opacity);
            return layer;
        }

        internal static JObject Write(JObject request)
        {
            string output = LocalPath((string)request["output"]);
            if (File.Exists(output) || !output.EndsWith(".mxd", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("Use a new MXD path.");
            IMapDocument document = new MapDocumentClass();
            document.New(output);
            try
            {
                int window = Program.GetDesktopWindow();
                ((IActiveView)document.PageLayout).Activate(window);
                var map = document.get_Map(0);
                map.Name = (string)request["name"] ?? "Layers";
                var wkt = (string)request["crs"];
                if (!String.IsNullOrEmpty(wkt))
                {
                    ISpatialReference reference;
                    int length;
                    var factory = (ISpatialReferenceFactory) new SpatialReferenceEnvironmentClass();
                    factory.CreateESRISpatialReference(wkt, out reference, out length);
                    map.SpatialReference = reference;
                }
                SetBackground(document, map, (string)request["background"]);
                foreach (JObject item in (JArray)request["layers"])
                {
                    var layer = BuildLayer(item);
                    map.AddLayer(layer);
                    layer.Visible = (bool?)item["visible"] ?? true;
                }
                var view = (IActiveView)map;
                view.Activate(window);
                document.SetActiveView(view);
                if (request["bounds"] is JArray extent && extent.Count == 4)
                {
                    IEnvelope box = new EnvelopeClass();
                    box.PutCoords((double)extent[0], (double)extent[1], (double)extent[2], (double)extent[3]);
                    box.SpatialReference = map.SpatialReference;
                    view.Extent = box;
                }
                document.Save(true, false);
            }
            finally { document.Close(); }
            return new JObject { ["output"] = output };
        }
    }
}
