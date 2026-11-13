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
            var renderer = feature == null ? null : feature.Renderer as ISimpleRenderer;
            if (renderer == null) return null;
            var marker = renderer.Symbol as ISimpleMarkerSymbol;
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
                return new JObject { ["color"] = Hex(marker.Color), ["shape"] = shape, ["size"] = marker.Size };
            }
            var fill = renderer.Symbol as ISimpleFillSymbol;
            var line = fill == null ? renderer.Symbol as ILineSymbol : fill.Outline;
            if (line == null) return null;
            return new JObject { ["color"] = Hex(line.Color), ["pattern"] = LineStyle(line as ISimpleLineSymbol),
                ["width"] = line.Width, ["fill"] = fill == null || fill.Style == esriSimpleFillStyle.esriSFSHollow ||
                    fill.Style == esriSimpleFillStyle.esriSFSNull ? null : Hex(fill.Color) };
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
            if (layer.Valid && feature != null) result["style"] = Appearance(layer);
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
                        ["bounds"] = Extent(((IActiveView)map).Extent), ["layers"] = layers });
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

        private static void SetStyle(ILayer layer, JObject style)
        {
            var feature = layer as IGeoFeatureLayer;
            if (feature == null || style == null) return;
            var color = Color((string)style["color"]);
            double width = (double?)style["width"] ?? 1.5;
            ISimpleLineSymbol line = new SimpleLineSymbolClass { Color = color, Width = width };
            switch ((string)style["pattern"])
            {
                case "dash": line.Style = esriSimpleLineStyle.esriSLSDash; break;
                case "dot": line.Style = esriSimpleLineStyle.esriSLSDot; break;
            }
            ISymbol symbol = (ISymbol)line;
            switch (feature.FeatureClass.ShapeType)
            {
                case esriGeometryType.esriGeometryPoint:
                case esriGeometryType.esriGeometryMultipoint:
                    ISimpleMarkerSymbol marker = new SimpleMarkerSymbolClass
                    { Color = color, Size = (double?)style["size"] ?? 8, Outline = false };
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
                    if (fill != null)
                    {
                        var tint = Color(fill);
                        tint.Transparency = 55;
                        area.Color = tint;
                    }
                    symbol = (ISymbol)area;
                    break;
            }
            feature.Renderer = (IFeatureRenderer)new SimpleRendererClass { Symbol = symbol };
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
                foreach (JObject item in (JArray)request["layers"])
                {
                    var layer = Program.OpenLayer(item);
                    SetStyle(layer, item["style"] as JObject);
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
