using System;
using System.IO;
using System.Runtime.InteropServices;
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
        [DllImport("user32.dll")]
        internal static extern int GetDesktopWindow();

        [STAThread]
        private static int Main(string[] args)
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            try
            {
                if (args.Length != 1)
                    throw new ArgumentException("Supply a local ArcGIS request.");
                var request = JObject.Parse(File.ReadAllText(args[0], Encoding.UTF8));
                if (!RuntimeManager.Bind(ProductCode.Desktop))
                    throw new InvalidOperationException("ArcGIS Desktop runtime could not be bound.");
                var license = new AoInitializeClass();
                try
                {
                    var product = Acquire(license);
                    JObject result;
                    switch ((string)request["action"])
                    {
                        case "view": result = WriteView(request); break;
                        case "read-mxd": result = Mxd.Read(request); break;
                        case "write-mxd": result = Mxd.Write(request); break;
                        case "layer":
                        case null: result = WriteLayer(request); break;
                        default: throw new ArgumentException("Unknown ArcGIS request.");
                    }
                    result["license"] = product.ToString();
                    Console.WriteLine(result.ToString(Formatting.None));
                    return 0;
                }
                finally { license.Shutdown(); }
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
                return 1;
            }
        }

        private static JObject WriteView(JObject request)
        {
            string output = Path.GetFullPath((string)request["output"]);
            if (!output.EndsWith(".mxd", StringComparison.OrdinalIgnoreCase) || File.Exists(output))
                throw new InvalidOperationException("Write to a new data view.");
            IMapDocument document = new MapDocumentClass();
            document.New(output);
            try
            {
                int window = GetDesktopWindow();
                ((IActiveView)document.PageLayout).Activate(window);
                var map = document.get_Map(0);
                map.Name = "Layers";
                var view = (IActiveView)map;
                view.Activate(window);
                document.SetActiveView(view);
                document.Save(true, false);
            }
            finally { document.Close(); }
            return new JObject { ["output"] = output };
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

        internal static ILayer OpenLayer(JObject request)
        {
            string source = Path.GetFullPath((string)request["source"]);
            ILayer layer;
            switch ((string)request["kind"])
            {
                case "vector":
                    if (!source.EndsWith(".gdb", StringComparison.OrdinalIgnoreCase) || !Directory.Exists(source))
                        throw new ArgumentException("A local file geodatabase is required.");
                    IWorkspaceFactory factory = new FileGDBWorkspaceFactoryClass();
                    var workspace = (IFeatureWorkspace)factory.OpenFromFile(source, 0);
                    var feature = new FeatureLayerClass();
                    feature.FeatureClass = workspace.OpenFeatureClass((string)request["dataset"]);
                    layer = feature;
                    break;
                case "raster":
                    if (!source.EndsWith(".tif", StringComparison.OrdinalIgnoreCase) || !File.Exists(source))
                        throw new ArgumentException("A local GeoTIFF is required.");
                    IRasterLayer raster = new RasterLayerClass();
                    raster.CreateFromFilePath(source);
                    layer = (ILayer)raster;
                    break;
                default:
                    throw new ArgumentException("Unknown result layer kind.");
            }
            if (!layer.Valid)
                throw new InvalidOperationException("ArcGIS cannot open the data source.");
            layer.Name = (string)request["name"];
            layer.Visible = (bool?)request["visible"] ?? true;
            return layer;
        }

        private static JObject WriteLayer(JObject request)
        {
            string output = Path.GetFullPath((string)request["output"]);
            if (!output.EndsWith(".lyr", StringComparison.OrdinalIgnoreCase) || File.Exists(output))
                throw new InvalidOperationException("Write to a new layer file.");
            ILayer layer = OpenLayer(request);
            ILayerFile document = new LayerFileClass();
            document.New(output);
            try
            {
                document.ReplaceContents(layer);
                document.Save();
            }
            finally { document.Close(); }
            return new JObject { ["output"] = output, ["name"] = layer.Name };
        }
    }
}
