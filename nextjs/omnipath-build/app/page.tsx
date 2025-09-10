import Link from 'next/link';
import { Database, ArrowRight, Layers, Activity, Zap, Shield } from 'lucide-react';

export default function Home() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900">
      <div className="absolute inset-0 bg-[url('/grid.svg')] bg-center [mask-image:linear-gradient(180deg,white,rgba(255,255,255,0))]"></div>
      
      <div className="relative">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20">
          <div className="text-center">
            <div className="inline-flex items-center gap-2 px-4 py-2 bg-white/10 backdrop-blur-sm rounded-full text-white/80 text-sm mb-8">
              <Zap className="w-4 h-4" />
              <span>Enterprise-grade data pipeline visualization</span>
            </div>
            
            <h1 className="text-5xl sm:text-7xl font-bold text-white mb-6">
              <span className="bg-gradient-to-r from-blue-400 via-purple-400 to-pink-400 bg-clip-text text-transparent">
                OmniPath
              </span>
              <br />
              <span className="text-3xl sm:text-5xl text-white/90">
                Database Visualizer
              </span>
            </h1>
            
            <p className="text-xl text-white/70 mb-12 max-w-2xl mx-auto">
              Transform your data journey from Bronze to Gold. Visualize, explore, and understand your data pipeline with our cutting-edge Parquet file viewer.
            </p>
            
            <div className="flex flex-col sm:flex-row gap-4 justify-center mb-20">
              <Link
                href="/dashboard"
                className="inline-flex items-center gap-2 px-8 py-4 bg-gradient-to-r from-blue-600 to-purple-600 text-white rounded-xl font-semibold hover:from-blue-700 hover:to-purple-700 transition-all transform hover:scale-105 shadow-lg"
              >
                Launch Dashboard
                <ArrowRight className="w-5 h-5" />
              </Link>
              <a
                href="https://github.com/hyparam/hyparquet"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 px-8 py-4 bg-white/10 backdrop-blur-sm text-white rounded-xl font-semibold hover:bg-white/20 transition-all border border-white/20"
              >
                Learn More
              </a>
            </div>
            
            <div className="grid grid-cols-1 md:grid-cols-3 gap-8 max-w-4xl mx-auto">
              <div className="bg-white/5 backdrop-blur-sm rounded-2xl p-6 border border-white/10">
                <div className="w-12 h-12 bg-gradient-to-br from-orange-400 to-amber-600 rounded-lg flex items-center justify-center mb-4 mx-auto">
                  <Database className="w-6 h-6 text-white" />
                </div>
                <h3 className="text-xl font-semibold text-white mb-2">Bronze Layer</h3>
                <p className="text-white/60">Raw data ingestion and storage</p>
              </div>
              
              <div className="bg-white/5 backdrop-blur-sm rounded-2xl p-6 border border-white/10">
                <div className="w-12 h-12 bg-gradient-to-br from-gray-400 to-slate-600 rounded-lg flex items-center justify-center mb-4 mx-auto">
                  <Layers className="w-6 h-6 text-white" />
                </div>
                <h3 className="text-xl font-semibold text-white mb-2">Silver Layer</h3>
                <p className="text-white/60">Cleaned and transformed data</p>
              </div>
              
              <div className="bg-white/5 backdrop-blur-sm rounded-2xl p-6 border border-white/10">
                <div className="w-12 h-12 bg-gradient-to-br from-yellow-400 to-amber-600 rounded-lg flex items-center justify-center mb-4 mx-auto">
                  <Activity className="w-6 h-6 text-white" />
                </div>
                <h3 className="text-xl font-semibold text-white mb-2">Gold Layer</h3>
                <p className="text-white/60">Business-ready analytics</p>
              </div>
            </div>
            
            <div className="mt-20 pt-20 border-t border-white/10">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-8 text-center">
                <div>
                  <div className="text-3xl font-bold text-white">3</div>
                  <div className="text-white/60">Data Layers</div>
                </div>
                <div>
                  <div className="text-3xl font-bold text-white">10+</div>
                  <div className="text-white/60">File Formats</div>
                </div>
                <div>
                  <div className="text-3xl font-bold text-white">100%</div>
                  <div className="text-white/60">Browser Native</div>
                </div>
                <div>
                  <div className="text-3xl font-bold text-white">
                    <Shield className="w-8 h-8 mx-auto" />
                  </div>
                  <div className="text-white/60">Secure</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
