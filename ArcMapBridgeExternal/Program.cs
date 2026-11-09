using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using ESRI.ArcGIS;
using ESRI.ArcGIS.Carto;
using ESRI.ArcGIS.DataSourcesGDB;
using ESRI.ArcGIS.Geodatabase;
using ESRI.ArcGIS.esriSystem;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace ArcMapHarness.ArcMapBridgeExternal
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            try
            {
                if (args.Length != 1)
                    throw new ArgumentException("Supply a local handoff request JSON file.");
                var request = JObject.Parse(File.ReadAllText(args[0], Encoding.UTF8));
                if (!RuntimeManager.Bind(ProductCode.Desktop))
                    throw new InvalidOperationException("ArcGIS Desktop runtime could not be bound.");
                var license = new AoInitializeClass();
                try
                {
                    var product = Acquire(license);
                    var result = Execute(request);
                    result["license"] = product.ToString();
                    Console.WriteLine(result.ToString(Formatting.None));
                    return 0;
                }
                finally
                {
                    license.Shutdown();
                }
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
                return 1;
            }
        }

        private static esriLicenseProductCode Acquire(IAoInitialize license)
        {
            foreach (var product in new[] { esriLicenseProductCode.esriLicenseProductCodeBasic,
                esriLicenseProductCode.esriLicenseProductCodeStandard, esriLicenseProductCode.esriLicenseProductCodeAdvanced })
            {
                if (license.IsProductCodeAvailable(product) == esriLicenseStatus.esriLicenseAvailable &&
                    license.Initialize(product) == esriLicenseStatus.esriLicenseCheckedOut)
                    return product;
            }
            throw new InvalidOperationException("No ArcGIS Desktop product license is available.");
        }

        private static JObject Execute(JObject request)
        {
            string file = Path.GetFullPath((string)request["map"]);
            if (!File.Exists(file) || !file.EndsWith(".mxd", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("A saved MXD file is required.");
            IMapDocument document = new MapDocumentClass();
            Console.Error.WriteLine("opening_mxd");
            document.Open(file, "");
            try
            {
                string action = (string)request["action"];
                if (action == "describe")
                    return Describe(document);
                if (action != "publish")
                    throw new ArgumentException("Unknown handoff action.");
                string output = Path.GetFullPath((string)request["output"]);
                if (!output.EndsWith(".mxd", StringComparison.OrdinalIgnoreCase) || File.Exists(output))
                    throw new InvalidOperationException("Publish to a new MXD file.");

                IMap map = document.get_Map((int)request["frame"]);
                var layers = Layers(map);
                string database = Path.GetFullPath((string)request["database"]);
                if (!Directory.Exists(database) || !database.EndsWith(".gdb", StringComparison.OrdinalIgnoreCase))
                    throw new ArgumentException("A local file geodatabase is required.");
                Console.Error.WriteLine("opening_feature_class");
                IWorkspaceFactory factory = new FileGDBWorkspaceFactoryClass();
                var workspace = (IFeatureWorkspace)factory.OpenFromFile(database, 0);
                var source = workspace.OpenFeatureClass((string)request["featureClass"]);
                IFeatureLayer feature = new FeatureLayerClass();
                feature.FeatureClass = source;
                feature.Name = (string)request["name"];
                feature.Visible = true;
                var styleIndex = (int?)request["style"];
                if (styleIndex.HasValue)
                {
                    var style = layers[styleIndex.Value] as IGeoFeatureLayer;
                    if (style == null || style.FeatureClass == null || style.FeatureClass.ShapeType != source.ShapeType)
                        throw new ArgumentException("Style layer and result must use the same geometry type.");
                    IFeatureRenderer original = style.Renderer;
                    if (original is IUniqueValueRenderer unique)
                        for (int field = 0; field < unique.FieldCount; field++)
                            RequireField(source, unique.get_Field(field));
                    if (original is IClassBreaksRenderer breaks)
                        RequireField(source, breaks.Field);
                    IObjectCopy copier = new ObjectCopyClass();
                    var renderer = copier.Copy(original) as IFeatureRenderer;
                    if (renderer == null)
                        throw new InvalidOperationException("The selected renderer cannot be cloned.");
                    ((IGeoFeatureLayer)feature).Renderer = renderer;
                    ((ILayerEffects)feature).Transparency = ((ILayerEffects)layers[styleIndex.Value]).Transparency;
                }
                map.AddLayer(feature);
                Console.Error.WriteLine("saving_mxd_copy");
                document.SaveAs(output, false, false);
                return new JObject { ["output"] = output, ["name"] = feature.Name, ["frame"] = (int)request["frame"] };
            }
            finally
            {
                document.Close();
            }
        }

        private static List<ILayer> Layers(IMap map)
        {
            var result = new List<ILayer>();
            IEnumLayer enumerator = map.get_Layers(null, true);
            for (ILayer layer = enumerator.Next(); layer != null; layer = enumerator.Next())
                result.Add(layer);
            return result;
        }

        private static void RequireField(IFeatureClass source, string name)
        {
            if (source.FindField(name) < 0)
                throw new ArgumentException("The result is missing a field used by the style: " + name);
        }

        private static JObject Describe(IMapDocument document)
        {
            var frames = new JArray();
            for (int frame = 0; frame < document.MapCount; frame++)
            {
                IMap map = document.get_Map(frame);
                var items = new JArray();
                var layers = Layers(map);
                for (int index = 0; index < layers.Count; index++)
                {
                    var layer = layers[index];
                    var feature = layer as IFeatureLayer;
                    var source = feature == null ? null : feature.FeatureClass as IDataset;
                    var filter = layer as IFeatureLayerDefinition;
                    var effects = layer as ILayerEffects;
                    var styled = layer as IGeoFeatureLayer;
                    var description = new JObject
                    {
                        ["index"] = index, ["name"] = layer.Name, ["valid"] = layer.Valid,
                        ["visible"] = layer.Visible, ["source"] = source == null ? null : source.Workspace.PathName,
                        ["dataset"] = source == null ? null : source.Name,
                        ["filter"] = filter == null ? null : filter.DefinitionExpression,
                        ["renderer"] = styled == null || styled.Renderer == null ? null
                            : styled.Renderer is ISimpleRenderer ? "simple"
                            : styled.Renderer is IUniqueValueRenderer ? "unique_value"
                            : styled.Renderer is IClassBreaksRenderer ? "class_breaks" : "other",
                        ["transparency"] = effects == null ? 0 : effects.Transparency
                    };
                    var selected = new JArray();
                    var selectable = layer as IFeatureSelection;
                    if (source != null && selectable != null && selectable.SelectionSet.Count > 0)
                    {
                        if (selectable.SelectionSet.Count > 100000)
                            throw new InvalidOperationException("ArcMap selection exceeds 100,000 features.");
                        IEnumIDs ids = selectable.SelectionSet.IDs;
                        for (int id = ids.Next(); id >= 0; id = ids.Next())
                            selected.Add(id);
                    }
                    description["selected"] = selected;
                    items.Add(description);
                }
                frames.Add(new JObject { ["index"] = frame, ["name"] = map.Name, ["layers"] = items });
            }
            return new JObject { ["frames"] = frames };
        }
    }
}
