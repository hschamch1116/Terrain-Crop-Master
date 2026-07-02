import os, tempfile
from qgis.utils import iface
from qgis.gui import QgsMapToolEmitPoint
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, 
    QgsGeometry, QgsRectangle, QgsSimpleFillSymbolLayer, QgsVectorFileWriter, 
    QgsRasterLayer, QgsCoordinateTransformContext, QgsCoordinateTransform,
    QgsWkbTypes, QgsMapSettings, QgsMapRendererCustomPainterJob
)
from PyQt5.QtWidgets import (QInputDialog, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLabel, QAction, QMessageBox, QFileDialog, QDialog, QComboBox, QProgressBar,
                             QLineEdit, QGridLayout, QShortcut, QCheckBox, QSlider)
from PyQt5.QtGui import QColor, QImage, QPainter, QKeySequence, QPixmap
from PyQt5.QtCore import Qt, QCoreApplication, QSize, QTimer
from osgeo import gdal

class ResolutionSelectDialog(QDialog):
    """ 최상위 레이어 팝업 포커스를 유지하며, 실시간 음영기복도 미리보기를 제공하는 설정창 """
    def __init__(self, default_idx=1, parent=None, bbox=None, dem_layer=None):
        super(ResolutionSelectDialog, self).__init__(parent)
        self.selected_value = "4096"
        self.dem_format = "BT"
        self.bbox = bbox
        self.dem_layer = dem_layer
        
        # 음영 기복 파라미터 기본값
        self.altitude = 45.0
        self.azimuth = 315.0
        self.z_factor = 1.0
        
        # 디바운싱용 타이머 탑재 (200ms 지연 렌더링)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.update_preview)
        
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowModality(Qt.ApplicationModal)
        self.init_ui(default_idx)
        
    def init_ui(self, default_idx):
        self.setWindowTitle("내보내기 설정")
        
        # 메인 레이아웃 (수평 배치로 좌측 설정 / 우측 미리보기 분할)
        self.main_layout = QHBoxLayout()
        
        # 1. 좌측 설정 판넬
        left_widget = QWidget(self)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        lbl = QLabel("내보낼 지형의 수평 픽셀(해상도) 크기:", self)
        lbl.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(lbl)
        
        self.combo = QComboBox(self)
        self.presets = ["2048", "4096", "8192", "직접 입력..."]
        self.combo.addItems(self.presets)
        self.combo.setCurrentIndex(default_idx)
        left_layout.addWidget(self.combo)
        
        lbl_format = QLabel("DEM (고도 데이터) 출력 포맷:", self)
        lbl_format.setStyleSheet("margin-top: 8px; font-weight: bold;")
        left_layout.addWidget(lbl_format)
        
        self.combo_format = QComboBox(self)
        self.combo_format.addItems(["CryEngine (.bt)", "GeoTIFF (.tif)", "다중방향 음영기복도 (.tif)"])
        self.combo_format.setCurrentIndex(0)
        self.combo_format.currentIndexChanged.connect(self.toggle_hillshade_options)
        left_layout.addWidget(self.combo_format)
        
        # 1-1. 음영기복도 전용 수치 조절 영역 (기본 숨김)
        self.hillshade_options_widget = QWidget(self)
        hs_layout = QVBoxLayout(self.hillshade_options_widget)
        hs_layout.setContentsMargins(0, 5, 0, 0)
        
        # 다중방향 토글 체크박스 (기본: 다중방향 사용)
        self.cb_multidirectional = QCheckBox("다중방향 광원 음영 (Multidirectional)", self)
        self.cb_multidirectional.setChecked(True)
        self.cb_multidirectional.toggled.connect(self.toggle_multidirectional)
        hs_layout.addWidget(self.cb_multidirectional)
        
        # 고도각 조절 (Altitude: 0 ~ 90)
        lbl_alt = QLabel("음영 고도각 (Altitude: 0 ~ 90°):", self)
        lbl_alt.setStyleSheet("margin-top: 5px;")
        self.txt_altitude = QLineEdit("45.0", self)
        self.txt_altitude.setFixedWidth(50)
        
        self.slider_altitude = QSlider(Qt.Horizontal, self)
        self.slider_altitude.setRange(0, 90)
        self.slider_altitude.setValue(45)
        
        alt_header_layout = QHBoxLayout()
        alt_header_layout.addWidget(lbl_alt)
        alt_header_layout.addStretch()
        alt_header_layout.addWidget(self.txt_altitude)
        hs_layout.addLayout(alt_header_layout)
        hs_layout.addWidget(self.slider_altitude)
        
        # 방위각 조절 영역 컨테이너화 (다중방향 시 동적 감춤 처리용)
        self.azimuth_container_widget = QWidget(self)
        azi_layout = QVBoxLayout(self.azimuth_container_widget)
        azi_layout.setContentsMargins(0, 0, 0, 0)
        
        lbl_azi = QLabel("음영 방위각 (Azimuth: 0 ~ 360°):", self)
        lbl_azi.setStyleSheet("margin-top: 5px;")
        self.txt_azimuth = QLineEdit("315.0", self)
        self.txt_azimuth.setFixedWidth(50)
        
        self.slider_azimuth = QSlider(Qt.Horizontal, self)
        self.slider_azimuth.setRange(0, 360)
        self.slider_azimuth.setValue(315)
        
        azi_header_layout = QHBoxLayout()
        azi_header_layout.addWidget(lbl_azi)
        azi_header_layout.addStretch()
        azi_header_layout.addWidget(self.txt_azimuth)
        
        azi_layout.addLayout(azi_header_layout)
        azi_layout.addWidget(self.slider_azimuth)
        hs_layout.addWidget(self.azimuth_container_widget)
        
        # Z 척도 조절 (Z Factor: 0.1 ~ 5.0)
        lbl_z = QLabel("Z축 스케일 배율 (Z Factor: 0.1 ~ 5.0):", self)
        lbl_z.setStyleSheet("margin-top: 5px;")
        self.txt_z_factor = QLineEdit("1.0", self)
        self.txt_z_factor.setFixedWidth(50)
        
        self.slider_z_factor = QSlider(Qt.Horizontal, self)
        self.slider_z_factor.setRange(1, 50)  # 10으로 나눠서 0.1~5.0 범위 매핑
        self.slider_z_factor.setValue(10)
        
        z_header_layout = QHBoxLayout()
        z_header_layout.addWidget(lbl_z)
        z_header_layout.addStretch()
        z_header_layout.addWidget(self.txt_z_factor)
        hs_layout.addLayout(z_header_layout)
        hs_layout.addWidget(self.slider_z_factor)
        
        left_layout.addWidget(self.hillshade_options_widget)
        self.hillshade_options_widget.hide()
        
        # 하단 버튼부
        btn_layout = QHBoxLayout()
        btn_ok = QPushButton("확인", self)
        btn_ok.setStyleSheet("background-color: #3182ce; color: white; font-weight: bold; height: 28px;")
        btn_ok.clicked.connect(self.on_accept)
        btn_cancel = QPushButton("취소", self)
        btn_cancel.setStyleSheet("height: 28px;")
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        left_layout.addLayout(btn_layout)
        
        self.main_layout.addWidget(left_widget)
        
        # 2. 우측 미리보기 판넬 (기본 숨김)
        self.preview_widget = QWidget(self)
        right_layout = QVBoxLayout(self.preview_widget)
        right_layout.setContentsMargins(5, 0, 0, 0)
        
        lbl_preview_title = QLabel("실시간 미리보기 (256x256)", self)
        lbl_preview_title.setStyleSheet("font-weight: bold; color: #4a5568;")
        lbl_preview_title.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(lbl_preview_title)
        
        self.lbl_preview = QLabel(self)
        self.lbl_preview.setFixedSize(256, 256)
        self.lbl_preview.setStyleSheet("background-color: #edf2f7; border: 1px solid #cbd5e0; border-radius: 4px;")
        self.lbl_preview.setAlignment(Qt.AlignCenter)
        
        # 빈 가이드 텍스트 장착
        self.lbl_preview.setText("DEM 레이어 없음\n(미리보기 불가)")
        right_layout.addWidget(self.lbl_preview)
        
        self.main_layout.addWidget(self.preview_widget)
        self.preview_widget.hide()
        
        self.setLayout(self.main_layout)
        self.resize(300, 160)
        
        # 시그널 바인딩 및 슬롯 동기화
        self.slider_altitude.valueChanged.connect(self.sync_slider_to_txt_alt)
        self.slider_azimuth.valueChanged.connect(self.sync_slider_to_txt_azi)
        self.slider_z_factor.valueChanged.connect(self.sync_slider_to_txt_z)
        
        self.txt_altitude.editingFinished.connect(self.sync_txt_to_slider_alt)
        self.txt_azimuth.editingFinished.connect(self.sync_txt_to_slider_azi)
        self.txt_z_factor.editingFinished.connect(self.sync_txt_to_slider_z)
        
        # 초기 다중방향 체크 상태이므로 방위각 조절 위젯을 기본적으로 숨김
        self.azimuth_container_widget.hide()
        
    def toggle_hillshade_options(self, index):
        if index == 2:
            self.hillshade_options_widget.show()
            self.preview_widget.show()
            self.resize(580, 390)
            self.preview_timer.start(50)  # 지연 가동
        else:
            self.hillshade_options_widget.hide()
            self.preview_widget.hide()
            self.resize(300, 160)
            
    def toggle_multidirectional(self, checked):
        # 다중방향 음영 시 방위각 설정 영역 숨김 (사용자 시각적 명확성 확보)
        self.azimuth_container_widget.setVisible(not checked)
        self.preview_timer.start(200)
        
    def sync_slider_to_txt_alt(self, val):
        self.txt_altitude.setText(f"{val:.1f}")
        self.altitude = float(val)
        self.preview_timer.start(200)  # 디바운싱 렌더링 호출
        
    def sync_slider_to_txt_azi(self, val):
        self.txt_azimuth.setText(f"{val:.1f}")
        self.azimuth = float(val)
        self.preview_timer.start(200)  # 디바운싱 렌더링 호출
        
    def sync_slider_to_txt_z(self, val):
        z_val = val / 10.0
        self.txt_z_factor.setText(f"{z_val:.1f}")
        self.z_factor = z_val
        self.preview_timer.start(200)  # 디바운싱 렌더링 호출
        
    def sync_txt_to_slider_alt(self):
        try:
            val = float(self.txt_altitude.text())
            val = max(0.0, min(90.0, val))
            self.txt_altitude.setText(f"{val:.1f}")
            self.altitude = val
            self.slider_altitude.blockSignals(True)
            self.slider_altitude.setValue(int(val))
            self.slider_altitude.blockSignals(False)
            self.preview_timer.start(200)
        except ValueError:
            self.txt_altitude.setText(f"{self.slider_altitude.value():.1f}")
            
    def sync_txt_to_slider_azi(self):
        try:
            val = float(self.txt_azimuth.text())
            val = max(0.0, min(360.0, val))
            self.txt_azimuth.setText(f"{val:.1f}")
            self.azimuth = val
            self.slider_azimuth.blockSignals(True)
            self.slider_azimuth.setValue(int(val))
            self.slider_azimuth.blockSignals(False)
            self.preview_timer.start(200)
        except ValueError:
            self.txt_azimuth.setText(f"{self.slider_azimuth.value():.1f}")
            
    def sync_txt_to_slider_z(self):
        try:
            val = float(self.txt_z_factor.text())
            val = max(0.1, min(5.0, val))
            self.txt_z_factor.setText(f"{val:.1f}")
            self.z_factor = val
            self.slider_z_factor.blockSignals(True)
            self.slider_z_factor.setValue(int(val * 10))
            self.slider_z_factor.blockSignals(False)
            self.preview_timer.start(200)
        except ValueError:
            self.txt_z_factor.setText(f"{(self.slider_z_factor.value() / 10.0):.1f}")
            
    def update_preview(self):

        if not self.dem_layer or not self.bbox:
            self.lbl_preview.setText("DEM 레이어 또는\n지형 영역이 유효하지 않음")
            return
            
        try:
            source_path = self.dem_layer.source()
            if not os.path.exists(source_path):
                if hasattr(self.dem_layer, 'dataProvider') and hasattr(self.dem_layer.dataProvider(), 'dataSourceUri'):
                    source_path = self.dem_layer.dataProvider().dataSourceUri().split("|")[0]
                    
            # 1. 256x256 크기 Warp 추출 (프로젝트 좌표계 재투영 강제 주입)
            dest_crs = iface.mapCanvas().mapSettings().destinationCrs()
            warp_opts = gdal.WarpOptions(
                format="GTiff",
                outputBounds=[self.bbox.xMinimum(), self.bbox.yMinimum(), self.bbox.xMaximum(), self.bbox.yMaximum()],
                width=256,
                height=256,
                resampleAlg=gdal.GRA_Bilinear,
                dstSRS=dest_crs.toWkt(),  # 대상 좌표계 강제 매칭
                cropToCutline=False
            )
            # 가상 메모리 파일에 Warp 작성
            warp_ds = gdal.Warp("/vsimem/preview_dem.tif", source_path, options=warp_opts)
            if warp_ds is None:
                self.lbl_preview.setText("Warp 연산 실패")
                return
                
            # 2. CRS 스케일 환산 및 gdal.DEMProcessing 수행
            is_multi = self.cb_multidirectional.isChecked()
            scale_val = 1.0
            if dest_crs.isGeographic():
                scale_val = 111120.0
                
            opts_dict = {
                "format": "GTiff",
                "alg": "zevenbergenThorne",
                "computeEdges": True,
                "zFactor": self.z_factor,
                "scale": scale_val,
                "altitude": self.altitude
            }
            if is_multi:
                opts_dict["multiDirectional"] = True
            else:
                opts_dict["azimuth"] = self.azimuth
                
            dem_opts = gdal.DEMProcessingOptions(**opts_dict)
            
            # 가상 메모리 파일에 직접 다중방향 음영 연산
            ds_shade = gdal.DEMProcessing("/vsimem/preview_shade.tif", warp_ds, "hillshade", options=dem_opts)
            warp_ds = None  # 해제
            gdal.Unlink("/vsimem/preview_dem.tif")  # 가상 메모리 DEM 청소
            
            # 3. 생성된 음영 메모리 밴드를 QImage로 변환해 화면에 렌더링
            if ds_shade is not None:
                band = ds_shade.GetRasterBand(1)
                if band:
                    data_bytes = band.ReadRaster(0, 0, 256, 256, buf_type=gdal.GDT_Byte)
                    ds_shade = None
                    gdal.Unlink("/vsimem/preview_shade.tif")  # 가상 메모리 Shade 청소
                    
                    qimg = QImage(data_bytes, 256, 256, QImage.Format_Grayscale8)
                    self.lbl_preview.setPixmap(QPixmap.fromImage(qimg))
                
        except Exception as err:
            self.lbl_preview.setText(f"미리보기 연산 에러:\n{str(err)[:50]}")
            print(f"❌ Preview render failure: {str(err)}")
            try:
                if os.path.exists(temp_dem):
                    os.remove(temp_dem)
                if os.path.exists(temp_shade):
                    os.remove(temp_shade)
            except:
                pass
            
    def on_accept(self):
        self.selected_value = self.combo.currentText()
        if self.combo_format.currentIndex() == 0:
            self.dem_format = "BT"
        elif self.combo_format.currentIndex() == 1:
            self.dem_format = "GTiff"
        else:
            self.dem_format = "Hillshade"
            
        try:
            self.altitude = float(self.txt_altitude.text())
            self.azimuth = float(self.txt_azimuth.text())
            self.z_factor = float(self.txt_z_factor.text())
        except ValueError:
            QMessageBox.critical(self, "오류", "고도각, 방위각, Z척도 값은 실수 형식이어야 합니다.")
            return
            
        self.accept()

class MultiSizeSelectDialog(QDialog):
    """ 크롭 박스 규격 다중 체크 및 일괄 생성 대화상자 """
    def __init__(self, parent=None):
        super(MultiSizeSelectDialog, self).__init__(parent)
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowModality(Qt.ApplicationModal)
        self.resize(260, 240)
        self.selected_sizes = []
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("크롭 박스 다중 크기 생성")
        layout = QVBoxLayout()
        
        lbl = QLabel("생성할 지형 영역(크롭 박스)의 크기를 선택하세요:", self)
        lbl.setStyleSheet("font-weight: bold; margin-bottom: 8px;")
        layout.addWidget(lbl)
        
        self.cb_2048 = QCheckBox("2048 m (Green)", self)
        self.cb_4096 = QCheckBox("4096 m (Red)", self)
        self.cb_8192 = QCheckBox("8192 m (Blue)", self)
        self.cb_15360 = QCheckBox("15360 m (Purple)", self)
        
        # 기본 체크값 설정
        self.cb_2048.setChecked(True)
        
        layout.addWidget(self.cb_2048)
        layout.addWidget(self.cb_4096)
        layout.addWidget(self.cb_8192)
        layout.addWidget(self.cb_15360)
        
        btn_layout = QHBoxLayout()
        btn_ok = QPushButton("일괄 생성", self)
        btn_ok.setStyleSheet("background-color: #3182ce; color: white; font-weight: bold;")
        btn_ok.clicked.connect(self.on_accept)
        
        btn_cancel = QPushButton("취소", self)
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        
        self.setLayout(layout)
        
    def on_accept(self):
        if self.cb_2048.isChecked():
            self.selected_sizes.append("2048")
        if self.cb_4096.isChecked():
            self.selected_sizes.append("4096")
        if self.cb_8192.isChecked():
            self.selected_sizes.append("8192")
        if self.cb_15360.isChecked():
            self.selected_sizes.append("15360")
            
        if not self.selected_sizes:
            QMessageBox.warning(self, "알림", "최소 하나 이상의 크기를 선택해야 합니다.")
            return
            
        self.accept()

class R16ConversionDialog(QDialog):
    """ 크라이엔진용 R16 변환기 다이얼로그 """
    def __init__(self, parent=None, default_input=""):
        super(R16ConversionDialog, self).__init__(parent)
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowModality(Qt.ApplicationModal)
        self.resize(550, 250)
        self.init_ui(default_input)

    def init_ui(self, default_input):
        self.setWindowTitle("CryEngine R16 Converter")
        
        layout = QVBoxLayout()
        grid = QGridLayout()
        
        # 입력 파일 레이아웃
        lbl_input = QLabel("입력 지형 파일 (.asc / .tif / .bt):", self)
        self.txt_input = QLineEdit(self)
        if default_input:
            self.txt_input.setText(default_input)
        btn_browse_input = QPushButton("찾아보기", self)
        btn_browse_input.clicked.connect(self.browse_input)
        
        grid.addWidget(lbl_input, 0, 0)
        grid.addWidget(self.txt_input, 0, 1)
        grid.addWidget(btn_browse_input, 0, 2)
        
        # 출력 파일 레이아웃
        lbl_output = QLabel("출력 R16 파일 (.r16):", self)
        self.txt_output = QLineEdit(self)
        if default_input:
            base, _ = os.path.splitext(default_input)
            self.txt_output.setText(base + "_terrain.r16")
        btn_browse_output = QPushButton("찾아보기", self)
        btn_browse_output.clicked.connect(self.browse_output)
        
        grid.addWidget(lbl_output, 1, 0)
        grid.addWidget(self.txt_output, 1, 1)
        grid.addWidget(btn_browse_output, 1, 2)
        
        # 최저 고도 레인지 레이아웃
        lbl_min_height = QLabel("최저 고도 값 (Min Height m):", self)
        self.txt_min_height = QLineEdit(self)
        self.txt_min_height.setText("0")
        grid.addWidget(lbl_min_height, 2, 0)
        grid.addWidget(self.txt_min_height, 2, 1)
        
        # 최대 고도 레인지 레이아웃
        lbl_max_height = QLabel("최대 고도 값 (Max Height m):", self)
        self.txt_max_height = QLineEdit(self)
        self.txt_max_height.setText("305")
        grid.addWidget(lbl_max_height, 3, 0)
        grid.addWidget(self.txt_max_height, 3, 1)
        
        layout.addLayout(grid)
        
        # 하단 정보 안내
        lbl_info = QLabel("※ 변환 시 자동 기능: 시계방향 90도 회전 / Little Endian 무손실 정렬 / 가비지 헤더 자동 삭제", self)
        lbl_info.setStyleSheet("color: blue; font-size: 11px;")
        lbl_info.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_info)
        
        # 버튼 레이아웃
        btn_layout = QHBoxLayout()
        self.btn_convert = QPushButton("크라이엔진용 RAW 변환 시작", self)
        self.btn_convert.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; font-size: 13px; height: 35px;")
        self.btn_convert.clicked.connect(self.run_conversion)
        
        btn_cancel = QPushButton("취소", self)
        btn_cancel.clicked.connect(self.reject)
        btn_cancel.setStyleSheet("height: 35px;")
        
        btn_layout.addWidget(self.btn_convert)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        
        self.setLayout(layout)
        
        if default_input:
            self.auto_detect_heights(default_input)

    def browse_input(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "지형 소스 파일 선택 (.asc, .tif, .tiff, .bt)", "", "Terrain Files (*.asc *.tif *.tiff *.bt);;All Files (*)"
        )
        if file_path:
            self.txt_input.setText(file_path)
            base, _ = os.path.splitext(file_path)
            self.txt_output.setText(base + "_terrain.r16")
            self.auto_detect_heights(file_path)

    def browse_output(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "출력 파일 저장 위치 지정", "", "CryEngine Raw 16bit (*.r16);;Raw Binary (*.raw)"
        )
        if file_path:
            self.txt_output.setText(file_path)

    def auto_detect_heights(self, file_path):
        try:
            if not os.path.exists(file_path):
                return
            dataset = gdal.Open(file_path)
            if dataset:
                band = dataset.GetRasterBand(1)
                if band:
                    min_val, max_val = band.ComputeRasterMinMax(True)
                    self.txt_min_height.setText(f"{min_val:.2f}")
                    self.txt_max_height.setText(f"{max_val:.2f}")
        except Exception as e:
            print(f"Failed to auto-detect min/max heights: {str(e)}")

    def run_conversion(self):
        input_file = self.txt_input.text()
        output_file = self.txt_output.text()
        min_height = self.txt_min_height.text()
        max_height = self.txt_max_height.text()

        if not input_file or not output_file or not min_height or not max_height:
            QMessageBox.critical(self, "에러", "모든 필드를 입력해주세요.")
            return

        output_dir = os.path.dirname(output_file)
        temp_tif = os.path.join(output_dir, "temp_rotated.tif")

        try:
            self.btn_convert.setText("변환 중... (1/2)")
            self.btn_convert.setEnabled(False)
            QCoreApplication.processEvents()

            # 1단계: gdal.Warp를 이용한 시계방향 90도 회전
            warp_options = gdal.WarpOptions(
                creationOptions=["FORCE_ORIENTATION=CW"],
                resampleAlg=gdal.GRA_Bilinear
            )
            gdal.Warp(temp_tif, input_file, options=warp_options)

            self.btn_convert.setText("변환 중... (2/2)")
            QCoreApplication.processEvents()

            # 2단계: gdal.Translate를 이용한 16비트 리틀엔디안 RAW 추출
            translate_options = gdal.TranslateOptions(
                format="ENVI",
                outputType=gdal.GDT_UInt16,
                creationOptions=["BYTEORDER=LSB"],
                scaleParams=[[float(min_height), float(max_height), 0.0, 65535.0]],
                noData=0
            )
            gdal.Translate(output_file, temp_tif, options=translate_options)

            # 가비지 컬렉션 (임시 ENVI 헤더 및 TIF 삭제)
            if os.path.exists(temp_tif):
                os.remove(temp_tif)
            temp_hdr = output_file + ".hdr"
            if os.path.exists(temp_hdr):
                os.remove(temp_hdr)
            base_no_ext, _ = os.path.splitext(output_file)
            if os.path.exists(base_no_ext + ".hdr"):
                os.remove(base_no_ext + ".hdr")

            QMessageBox.information(
                self, "성공", f"변환이 완료되었습니다!\n\n▶ 입력 범위: {min_height}m ~ {max_height}m\n▶ 출력 경로: {output_file}"
            )
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "변환 실패", f"GDAL 연산 중 오류가 발생했습니다.\n{str(e)}")
            # 임시 파일 정리
            if os.path.exists(temp_tif):
                try: os.remove(temp_tif)
                except: pass
        finally:
            self.btn_convert.setText("크라이엔진용 RAW 변환 시작")
            self.btn_convert.setEnabled(True)

class TerrainEditController(QWidget):
    """ DEM과 항공사진 다중 일괄 및 수동 내보내기를 지원하는 마스터 UI """
    def __init__(self, layer, size_label, line_color, plugin_ref, parent=iface.mainWindow()):
        super(TerrainEditController, self).__init__(parent, Qt.Window)
        self.layer = layer
        self.size_label = size_label
        self.line_color = line_color
        self.plugin_ref = plugin_ref 
        self.init_ui()
        
        # Listen to project layer changes to keep dropdown in sync
        QgsProject.instance().layersAdded.connect(self.refresh_layers)
        QgsProject.instance().layersRemoved.connect(self.refresh_layers)
        
    def closeEvent(self, event):
        try:
            QgsProject.instance().layersAdded.disconnect(self.refresh_layers)
        except:
            pass
        try:
            QgsProject.instance().layersRemoved.disconnect(self.refresh_layers)
        except:
            pass
        try:
            iface.layerTreeView().selectionModel().selectionChanged.disconnect(self.update_scratch_button_state)
        except:
            pass
        super(TerrainEditController, self).closeEvent(event)
        
    def is_layer_valid(self):
        try:
            return self.layer is not None and self.layer.id() is not None
        except RuntimeError:
            self.layer = None
            return False
        
    def init_ui(self):
        self.setWindowTitle("지형 박스 제어 센터")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.resize(280, 450)
        
        # QSS 테마 스타일시트 주입
        self.setStyleSheet("""
            QWidget {
                font-family: 'Malgun Gothic', 'Segoe UI', sans-serif;
                font-size: 11px;
                background-color: #f8f9fa;
                color: #2d3748;
            }
            QLabel {
                font-weight: 500;
                color: #4a5568;
            }
            QComboBox {
                background-color: #ffffff;
                border: 1px solid #cbd5e0;
                border-radius: 4px;
                padding: 4px 8px;
                min-height: 25px;
            }
            QComboBox:hover {
                border-color: #3182ce;
            }
            QPushButton {
                background-color: #ffffff;
                border: 1px solid #cbd5e0;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
                min-height: 28px;
            }
            QPushButton:hover {
                background-color: #edf2f7;
                border-color: #a0aec0;
            }
            QPushButton:pressed {
                background-color: #e2e8f0;
            }
            QPushButton:disabled {
                background-color: #cbd5e0;
                color: #718096;
                border: none;
            }
            
            QPushButton#btn_create_box {
                background-color: #3182ce;
                color: white;
                border: none;
            }
            QPushButton#btn_create_box:hover { background-color: #2b6cb0; }
            
            QPushButton#btn_edit {
                background-color: #3182ce;
                color: white;
                border: none;
            }
            QPushButton#btn_edit:hover { background-color: #2b6cb0; }
            QPushButton#btn_edit:checked {
                background-color: #e53e3e;
                color: white;
            }
            QPushButton#btn_edit:checked:hover { background-color: #c53030; }

            QPushButton#btn_move {
                background-color: #4a5568;
                color: white;
                border: none;
            }
            QPushButton#btn_move:hover { background-color: #2d3748; }
            
            QPushButton#btn_save_shp {
                background-color: #dd6b20;
                color: white;
                border: none;
            }
            QPushButton#btn_save_shp:hover { background-color: #c05621; }
            
            QPushButton#btn_save_scratch {
                background-color: #38a169;
                color: white;
                border: none;
            }
            QPushButton#btn_save_scratch:hover { background-color: #2f855a; }
            
            QPushButton#btn_manual_export {
                background-color: #4a5568;
                color: white;
                border: none;
            }
            QPushButton#btn_manual_export:hover { background-color: #2d3748; }

            QPushButton#btn_export {
                background-color: #2b6cb0;
                color: white;
                border: none;
            }
            QPushButton#btn_export:hover { background-color: #2c5282; }
            
            QPushButton#btn_convert_r16 {
                background-color: #805ad5;
                color: white;
                border: none;
            }
            QPushButton#btn_convert_r16:hover { background-color: #6b46c1; }

            QPushButton#btn_close {
                background-color: #718096;
                color: white;
                border: none;
            }
            QPushButton#btn_close:hover { background-color: #4a5568; }
            
            QProgressBar {
                border: 1px solid #cbd5e0;
                border-radius: 4px;
                text-align: center;
                background-color: #ffffff;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #48bb78;
                border-radius: 3px;
            }
        """)
        
        layout = QVBoxLayout()
        
        # 크롭바운드 레이어 선택 드롭다운
        lbl_select = QLabel("크롭바운드(지형 영역) 레이어 선택:", self)
        lbl_select.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl_select)
        
        self.combo_layers = QComboBox(self)
        self.combo_layers.currentIndexChanged.connect(self.on_layer_changed)
        layout.addWidget(self.combo_layers)
        
        self.lbl_status = QLabel("", self)
        self.lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_status)
        
        # 새 지형 박스 생성 버튼
        self.btn_create_box = QPushButton("➕ 새 크롭 박스(영역) 생성", self)
        self.btn_create_box.setObjectName("btn_create_box")
        self.btn_create_box.clicked.connect(self.create_new_box)
        layout.addWidget(self.btn_create_box)
        
        self.btn_edit = QPushButton("✏️ 편집 모드 켜기/끄기", self)
        self.btn_edit.setObjectName("btn_edit")
        self.btn_edit.setCheckable(True)
        self.btn_edit.clicked.connect(self.toggle_edit)
        layout.addWidget(self.btn_edit)
        
        self.btn_move = QPushButton("🎯 마우스로 사각형 이동", self)
        self.btn_move.setObjectName("btn_move")
        self.btn_move.clicked.connect(self.activate_move_tool)
        layout.addWidget(self.btn_move)
        
        self.btn_save_shp = QPushButton("💾 현재 박스 SHP 파일로 저장", self)
        self.btn_save_shp.setObjectName("btn_save_shp")
        self.btn_save_shp.clicked.connect(self.save_current_box_to_shp)
        layout.addWidget(self.btn_save_shp)
        
        self.btn_save_scratch = QPushButton("💾 선택한 임시 레이어 저장", self)
        self.btn_save_scratch.setObjectName("btn_save_scratch")
        self.btn_save_scratch.clicked.connect(self.save_selected_scratch_layers)
        layout.addWidget(self.btn_save_scratch)
        
        self.btn_manual_export = QPushButton("⚙️ 수동 내보내기 설정 및 실행", self)
        self.btn_manual_export.setObjectName("btn_manual_export")
        self.btn_manual_export.clicked.connect(lambda: self.export_multiple_layers(manual_only=True))
        layout.addWidget(self.btn_manual_export)
        
        self.btn_export = QPushButton("🚀 DEM + 항공사진 자동 일괄 내보내기", self)
        self.btn_export.setObjectName("btn_export")
        self.btn_export.clicked.connect(lambda: self.export_multiple_layers(manual_only=False))
        layout.addWidget(self.btn_export)
        
        self.btn_convert_r16 = QPushButton("🎮 크라이엔진용 R16 변환기", self)
        self.btn_convert_r16.setObjectName("btn_convert_r16")
        self.btn_convert_r16.clicked.connect(self.open_r16_converter)
        layout.addWidget(self.btn_convert_r16)
        
        self.progress_label = QLabel("저장 대기 중...", self)
        layout.addWidget(self.progress_label)
        
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        btn_close = QPushButton("🔒 컨트롤러 닫기", self)
        btn_close.setObjectName("btn_close")
        btn_close.clicked.connect(self.close_controller)
        layout.addWidget(btn_close)
        
        self.setLayout(layout)
        
        # 툴팁 추가 및 단축키 안내
        self.btn_create_box.setToolTip("지도 상에서 마우스 클릭으로 정사각형 크롭 영역(박스)을 새로 생성합니다.")
        self.btn_edit.setToolTip("선택된 크롭 박스 레이어의 편집 모드를 켜서 사각형 크기를 수동 조절하거나, 완료 후 저장하고 끕니다. (Ctrl+E)")
        self.btn_move.setToolTip("선택된 크롭 박스를 드래그하여 지도 위에서 다른 위치로 자유롭게 이동시킵니다. (Ctrl+M)")
        self.btn_save_shp.setToolTip("현재 임시 사각 영역 레이어를 정식 Shapefile(.shp) 파일로 컴퓨터에 영구 저장합니다.")
        self.btn_save_scratch.setToolTip("레이어 패널에서 선택한 임시 메모리 레이어(.gpkg/.shp)를 영구 저장 및 교체합니다. (원래 스타일/라벨 유지) (Ctrl+S)")
        self.btn_manual_export.setToolTip("레이어 패널에서 내가 직접 선택한 래스터/벡터 레이어들을 현재 설정된 영역대로 크롭 내보내기합니다.")
        self.btn_export.setToolTip("프로젝트 내의 DEM과 위성/항공사진을 찾아 현재 사각 영역 크기에 맞춰 원터치 일괄 내보내기합니다.")
        self.btn_convert_r16.setToolTip("지형 고도 데이터를 크라이엔진(CryEngine) 전용 Raw 16bit(.r16) 규격으로 변환합니다.")

        # 단축키 설정 및 바인딩
        self.shortcut_edit = QShortcut(QKeySequence("Ctrl+E"), self)
        self.shortcut_edit.activated.connect(self.btn_edit.click)
        
        self.shortcut_move = QShortcut(QKeySequence("Ctrl+M"), self)
        self.shortcut_move.activated.connect(self.btn_move.click)
        
        self.shortcut_save_scratch = QShortcut(QKeySequence("Ctrl+S"), self)
        self.shortcut_save_scratch.activated.connect(self.btn_save_scratch.click)

        # 실시간 선택 변경 감지 시그널 연결
        try:
            iface.layerTreeView().selectionModel().selectionChanged.connect(self.update_scratch_button_state)
        except Exception as e:
            print(f"⚠️ 레이어 선택 시그널 바인딩 실패: {str(e)}")

        self.refresh_layers()
        self.update_buttons_state()
        self.show()

    def refresh_layers(self):
        self.combo_layers.blockSignals(True)
        self.combo_layers.clear()
        self.combo_layers.addItem("선택 안 함 (박스 지정 없음)", None)
        
        project = QgsProject.instance()
        polygon_layers = []
        
        # self.layer가 실제 프로젝트 내에 여전히 유효한지 확인
        layer_exists = False
        
        for layer in project.mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == QgsWkbTypes.PolygonGeometry:
                polygon_layers.append(layer)
                if self.is_layer_valid() and layer.id() == self.layer.id():
                    layer_exists = True
                    
        if not layer_exists:
            self.layer = None
            
        # 가나다순으로 레이어 정렬
        polygon_layers.sort(key=lambda l: l.name())
        
        selected_index = 0
        for idx, layer in enumerate(polygon_layers):
            self.combo_layers.addItem(layer.name(), layer.id())
            if self.is_layer_valid() and layer.id() == self.layer.id():
                selected_index = idx + 1 # "선택 안 함"이 0번 인덱스이므로
                
        self.combo_layers.setCurrentIndex(selected_index)
        self.combo_layers.blockSignals(False)

    def on_layer_changed(self, index):
        layer_id = self.combo_layers.currentData()
        if layer_id:
            project = QgsProject.instance()
            selected_layer = project.mapLayer(layer_id)
            if selected_layer:
                # 레이어 이름으로부터 크기 라벨 유추 시도
                size_label = "2048"
                name = selected_layer.name()
                if "m" in name:
                    parts = name.split("_")
                    for part in parts:
                        if part.endswith("m"):
                            size_label = part[:-1]
                # 렌더러 선 색상 유추 시도
                line_color = QColor(0, 200, 0, 255)
                try:
                    symbol = selected_layer.renderer().symbol()
                    line_color = symbol.symbolLayer(0).strokeColor()
                except:
                    pass
                
                self.layer = selected_layer
                self.size_label = size_label
                self.line_color = line_color
                self.update_buttons_state()
                return
                
        self.layer = None
        self.update_buttons_state()

    def update_buttons_state(self):
        if self.is_layer_valid():
            self.lbl_status.setText(f"임시 배치 상태 ({self.size_label}m)" if "Temp" in self.layer.name() else f"정식 SHP: {self.layer.name()}")
            
            self.btn_edit.setEnabled(True)
            self.btn_edit.setChecked(self.layer.isEditable())
            self.btn_edit.setText("🛑 편집 저장 및 종료" if self.layer.isEditable() else "✏️ 편집 모드 켜기")
            
            self.btn_move.setEnabled(True)
            self.btn_save_shp.setEnabled(True)
            self.btn_manual_export.setEnabled(True)
            self.btn_export.setEnabled(True)
        else:
            self.lbl_status.setText("크롭 박스가 생성되지 않았습니다.")
            
            self.btn_edit.setEnabled(False)
            self.btn_edit.setChecked(False)
            self.btn_edit.setText("✏️ 편집 모드 켜기")
            
            self.btn_move.setEnabled(False)
            self.btn_save_shp.setEnabled(False)
            self.btn_manual_export.setEnabled(False)
            self.btn_export.setEnabled(False)

    def set_active_layer(self, layer, size_label, line_color):
        self.layer = layer
        self.size_label = size_label
        self.line_color = line_color
        self.update_buttons_state()

    def create_new_box(self):
        canvas = iface.mapCanvas()
        self.tool = ClickToSquareColorPresetTool(canvas, self.plugin_ref)
        canvas.setMapTool(self.tool)
        
    def toggle_edit(self):
        if not self.is_layer_valid(): return
        if self.layer.isEditable():
            self.layer.commitChanges()
            self.btn_edit.setText("✏️ 편집 모드 켜기")
        else:
            iface.setActiveLayer(self.layer)
            self.layer.startEditing()
            self.btn_edit.setText("🛑 편집 저장 및 종료")
            
    def activate_move_tool(self):
        if not self.is_layer_valid(): return
        if not self.layer.isEditable():
            iface.setActiveLayer(self.layer)
            self.layer.startEditing()
            self.btn_edit.setChecked(True)
            self.btn_edit.setText("🛑 편집 저장 및 종료")
        try:
            iface.actionMoveFeature().trigger()
            return
        except:
            pass
        try:
            for action in iface.digitizeToolBar().actions():
                if "mActionMoveFeature" in action.objectName() or "이동" in action.text():
                    action.trigger()
                    return
        except:
            pass

    def save_current_box_to_shp(self):
        if not self.is_layer_valid(): return
        if self.layer.isEditable():
            self.layer.commitChanges()
            self.btn_edit.setChecked(False)
            self.btn_edit.setText("✏️ 편집 모드 켜기")

        active_layer = iface.activeLayer()
        save_dir = os.path.expanduser("~/Documents")
        if active_layer and os.path.exists(os.path.dirname(active_layer.source())):
            save_dir = os.path.dirname(active_layer.source())

        idx = 1
        shp_path = os.path.join(save_dir, f"CropBounds_{self.size_label}m_{idx}.shp")
        while os.path.exists(shp_path):
            idx += 1
            shp_path = os.path.join(save_dir, f"CropBounds_{self.size_label}m_{idx}.shp")

        crs = iface.mapCanvas().mapSettings().destinationCrs()
        
        # QGIS3 최신 API 규격에 맞게 writeAsVectorFormatV3로 수정하여 백업 안정성 확보
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "ESRI Shapefile"
        options.fileEncoding = "UTF-8"
        
        # CRS 변환 설정 적용
        options.ct = QgsCoordinateTransform(self.layer.crs(), crs, QgsProject.instance())
        
        result = QgsVectorFileWriter.writeAsVectorFormatV3(
            self.layer, shp_path, QgsProject.instance().transformContext(), options
        )
        error = result[0]
        error_string = result[1] if len(result) > 1 else ""
        
        if error == QgsVectorFileWriter.NoError:
            final_shp_layer = QgsVectorLayer(shp_path, os.path.basename(shp_path), "ogr")
            symbol = final_shp_layer.renderer().symbol()
            stroke_symbol = QgsSimpleFillSymbolLayer()
            stroke_symbol.setFillColor(QColor(0, 0, 0, 0))
            stroke_symbol.setStrokeColor(self.line_color)
            stroke_symbol.setStrokeWidth(1.5)
            symbol.changeSymbolLayer(0, stroke_symbol)
            
            QgsProject.instance().addMapLayer(final_shp_layer)
            QgsProject.instance().removeMapLayer(self.layer.id())
            
            QMessageBox.information(self, "백업 성공", f"이동 조정된 박스가 정식 벡터 레이어로 저장되었습니다!\n\n▶ 경로: {shp_path}")
            
            self.layer = final_shp_layer
            self.lbl_status.setText(f"정식 SHP: {os.path.basename(shp_path)}")
        else:
            QMessageBox.critical(self, "저장 에러", f"SHP 라이팅 작업 중 내부 IO 오류가 발생했습니다.\n{error_string}")

    def save_selected_scratch_layers(self):
        selected_layers = iface.layerTreeView().selectedLayers()
        if not selected_layers:
            QMessageBox.warning(self, "알림", "선택된 레이어가 없습니다.\n레이어 패널에서 저장할 임시 스크레치 레이어를 선택해 주세요.")
            return

        scratch_layers = []
        for layer in selected_layers:
            if isinstance(layer, QgsVectorLayer):
                is_temp = layer.isTemporary() or (layer.dataProvider() and layer.dataProvider().name() == 'memory')
                if is_temp:
                    scratch_layers.append(layer)

        if not scratch_layers:
            QMessageBox.warning(self, "알림", "선택한 레이어 중 저장되지 않은 임시 스크레치 레이어가 없습니다.")
            return

        project = QgsProject.instance()
        success_count = 0

        for layer in scratch_layers:
            default_dir = os.path.expanduser("~/Documents")
            if project.fileName():
                default_dir = os.path.dirname(project.fileName())
            
            default_path = os.path.join(default_dir, f"{layer.name()}.gpkg")
            
            file_path, selected_filter = QFileDialog.getSaveFileName(
                self,
                f"임시 레이어 영구 저장: {layer.name()}",
                default_path,
                "GeoPackage (*.gpkg);;ESRI Shapefile (*.shp)"
            )
            
            if not file_path:
                continue

            driver_name = "GPKG"
            if file_path.lower().endswith(".shp"):
                driver_name = "ESRI Shapefile"

            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = driver_name
            options.fileEncoding = "UTF-8"
            options.ct = QgsCoordinateTransform(layer.crs(), layer.crs(), project)

            result = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, file_path, project.transformContext(), options
            )
            
            error = result[0]
            error_string = result[1] if len(result) > 1 else ""

            if error == QgsVectorFileWriter.NoError:
                new_layer = QgsVectorLayer(file_path, layer.name(), "ogr")
                if not new_layer.isValid():
                    QMessageBox.critical(self, "오류", f"저장된 레이어를 불러오는데 실패했습니다: {file_path}")
                    continue

                if layer.renderer():
                    new_layer.setRenderer(layer.renderer().clone())
                if layer.labelsEnabled() and layer.labeling():
                    new_layer.setLabeling(layer.labeling().clone())
                    new_layer.setLabelsEnabled(True)

                root = project.layerTreeRoot()
                node = root.findLayer(layer.id())
                if node:
                    parent = node.parent()
                    try:
                        idx = parent.children().index(node)
                        project.addMapLayer(new_layer, False)
                        parent.insertLayer(idx, new_layer)
                        project.removeMapLayer(layer.id())
                    except ValueError:
                        project.addMapLayer(new_layer)
                        project.removeMapLayer(layer.id())
                else:
                    project.addMapLayer(new_layer)
                    project.removeMapLayer(layer.id())

                success_count += 1
            else:
                QMessageBox.critical(
                    self, 
                    "저장 실패", 
                    f"레이어 '{layer.name()}' 저장 중 오류가 발생했습니다.\n{error_string}"
                )

        if success_count > 0:
            QMessageBox.information(
                self, 
                "저장 완료", 
                f"성공적으로 {success_count}개의 임시 레이어를 영구 레이어로 저장했습니다."
            )

    def update_scratch_button_state(self):
        try:
            selected_layers = iface.layerTreeView().selectedLayers()
            scratch_count = 0
            for layer in selected_layers:
                if isinstance(layer, QgsVectorLayer):
                    is_temp = layer.isTemporary() or (layer.dataProvider() and layer.dataProvider().name() == 'memory')
                    if is_temp:
                        scratch_count += 1
                        
            if scratch_count > 0:
                self.btn_save_scratch.setText(f"💾 선택한 임시 레이어 저장 ({scratch_count}개 선택됨)")
            else:
                self.btn_save_scratch.setText("💾 선택한 임시 레이어 저장")
        except Exception as e:
            print(f"⚠️ 임시 레이어 상태 업데이트 에러: {str(e)}")

    def export_multiple_layers(self, manual_only=False):
        """ 다중 선택되거나 감지된 DEM + 항공사진을 설정 창을 통해 동시 추출 저장 """
        if not self.is_layer_valid():
            QMessageBox.warning(self, "알림", "크롭 기준이 되는 지형 박스 레이어가 없습니다. 먼저 박스를 생성해 주세요.")
            return

        if self.layer.isEditable():
            self.layer.commitChanges()
            self.btn_edit.setChecked(False)
            self.btn_edit.setText("✏️ 편집 모드 켜기")

        selected_layers = iface.layerTreeView().selectedLayers()
        # Raster 및 Vector 레이어 모두 포함 (단, cropbox 레이어 자체는 제외)
        target_layers = [l for l in selected_layers if l.id() != self.layer.id() and (isinstance(l, QgsRasterLayer) or isinstance(l, QgsVectorLayer))]
        
        if manual_only:
            if not target_layers:
                QMessageBox.warning(self, "알림", "수동 내보내기를 하려면 QGIS 레이어 패널에서 크롭하고자 하는 레이어(래스터/벡터)를 선택한 후 실행해 주세요.")
                return
        else:
            if not target_layers:
                all_layers = QgsProject.instance().mapLayers().values()
                target_layers = [l for l in all_layers if l.id() != self.layer.id() and (isinstance(l, QgsRasterLayer) or isinstance(l, QgsVectorLayer))]

        if not target_layers:
            QMessageBox.warning(self, "알림", "자르고 싶은 DEM 레이어, 항공사진(래스터), 혹은 Shapefile(벡터) 레이어가 존재하지 않습니다.")
            return

        features = list(self.layer.getFeatures())
        if not features:
            QMessageBox.critical(self, "에러", "정사각형 박스 피처 데이터가 없습니다.")
            return
        bbox = features[0].geometry().boundingBox()

        # 래스터 레이어 목록 중 DEM 레이어가 있는지 색출
        dem_layer = None
        for l in target_layers:
            if isinstance(l, QgsRasterLayer):
                layer_name = l.name().lower()
                is_dem_temp = (len([rl for rl in target_layers if isinstance(rl, QgsRasterLayer)]) == 1) or any(
                    k in layer_name for k in [
                        "dem", "dsm", "dtm", "height", "높이", "수치표고", 
                        "vworld_dem", "elevation", "지형", "고도", "terrain", "grid"
                    ]
                )
                if is_dem_temp:
                    dem_layer = l
                    break

        resolution_presets = ["2048", "4096", "8192", "직접 입력..."]
        idx = 1
        for i, preset in enumerate(resolution_presets):
            if preset in self.size_label:
                idx = i
                break
        diag = ResolutionSelectDialog(default_idx=idx, parent=self, bbox=bbox, dem_layer=dem_layer)
        if not diag.exec_(): return
        
        selected_item = diag.selected_value
        dem_format = diag.dem_format
        if selected_item == "직접 입력...":
            custom_dialog = QInputDialog(self)
            custom_dialog.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
            custom_dialog.setWindowModality(Qt.ApplicationModal)
            custom_dialog.setWindowTitle("해상도 직접 입력")
            custom_dialog.setLabelText("원하는 수평 픽셀 수를 입력하세요:")
            custom_dialog.setIntRange(128, 16384)
            custom_dialog.setIntValue(2048)
            if not custom_dialog.exec_(): return
            pixel_size = custom_dialog.intValue()
        else:
            pixel_size = int(selected_item)

        export_dir = QFileDialog.getExistingDirectory(self, "결과물(.bt / .tif)을 일괄 저장할 폴더 선택", "")
        if not export_dir: return

        success_count = 0
        total_tasks = len(target_layers)
        exported_dem_path = None
        
        for i, target_layer in enumerate(target_layers):
            is_vector = isinstance(target_layer, QgsVectorLayer)
            is_dem = False
            
            if is_vector:
                file_name = f"{target_layer.name()}_{self.size_label}m_{pixel_size}.tif"
            else:
                layer_name = target_layer.name().lower()
                # 선택된 래스터 레이어가 1개 뿐인 경우 이름과 무관하게 DEM으로 처리하여 편리성 증대
                raster_count = len([l for l in target_layers if isinstance(l, QgsRasterLayer)])
                is_dem = (raster_count == 1) or any(
                    k in layer_name for k in [
                        "dem", "dsm", "dtm", "height", "높이", "수치표고", 
                        "vworld_dem", "elevation", "지형", "고도", "terrain", "grid"
                    ]
                )
                if is_dem:
                    if dem_format == "BT":
                        file_name = f"Terrain_{self.size_label}m_{pixel_size}.bt"
                        gdal_format = "BT"
                    elif dem_format == "GTiff":
                        file_name = f"Terrain_{self.size_label}m_{pixel_size}.tif"
                        gdal_format = "GTiff"
                    else: # "Hillshade"
                        file_name = f"Hillshade_{self.size_label}m_{pixel_size}.tif"
                        gdal_format = "GTiff"
                else:
                    file_name = f"Satellite_{self.size_label}m_{pixel_size}.tif"
                    gdal_format = "GTiff"
                
            out_path = os.path.join(export_dir, file_name)

            self.progress_label.setText(f"가공 중 ({i+1}/{total_tasks}): {target_layer.name()}")
            self.progress_bar.setValue(int((i / total_tasks) * 100))
            QCoreApplication.processEvents()

            try:
                if is_vector:
                    # QImage로 QGIS 벡터 스타일 그대로 고해상도 렌더링
                    image = QImage(QSize(pixel_size, pixel_size), QImage.Format_ARGB32_Premultiplied)
                    image.fill(Qt.transparent)
                    
                    painter = QPainter(image)
                    
                    settings = QgsMapSettings()
                    settings.setLayers([target_layer])
                    settings.setExtent(bbox)
                    settings.setOutputSize(QSize(pixel_size, pixel_size))
                    settings.setDestinationCrs(iface.mapCanvas().mapSettings().destinationCrs())
                    settings.setBackgroundColor(QColor(0, 0, 0, 0))
                    
                    job = QgsMapRendererCustomPainterJob(settings, painter)
                    job.start()
                    job.waitForFinished()
                    painter.end()
                    
                    # TIFF 저장
                    image.save(out_path, "TIFF")
                    
                    # GDAL GeoTransform & CRS Projection 정보 삽입하여 완벽한 GeoTIFF 제작
                    ds = gdal.Open(out_path, gdal.GA_Update)
                    if ds:
                        x_pixel_size = bbox.width() / float(pixel_size)
                        y_pixel_size = bbox.height() / float(pixel_size)
                        geotransform = [bbox.xMinimum(), x_pixel_size, 0.0, bbox.yMaximum(), 0.0, -y_pixel_size]
                        ds.SetGeoTransform(geotransform)
                        
                        crs = iface.mapCanvas().mapSettings().destinationCrs()
                        ds.SetProjection(crs.toWkt())
                        ds = None
                        
                    iface.addRasterLayer(out_path, os.path.basename(out_path))
                    success_count += 1
                else:
                    source_path = target_layer.source()
                    if not os.path.exists(source_path):
                        if hasattr(target_layer, 'dataProvider') and hasattr(target_layer.dataProvider(), 'dataSourceUri'):
                            source_path = target_layer.dataProvider().dataSourceUri()

                    def sub_callback(df_complete, msg, unknown):
                        step_percent = int(((i + df_complete) / total_tasks) * 100)
                        self.progress_bar.setValue(step_percent)
                        QCoreApplication.processEvents()
                        return 1

                    dest_crs = iface.mapCanvas().mapSettings().destinationCrs()
                    resample_alg = gdal.GRA_Bilinear if is_dem else gdal.GRA_NearestNeighbour
                    warp_options = gdal.WarpOptions(
                        format=gdal_format,
                        outputBounds=[bbox.xMinimum(), bbox.yMinimum(), bbox.xMaximum(), bbox.yMaximum()],
                        width=pixel_size,   
                        height=pixel_size,  
                        cropToCutline=False,
                        resampleAlg=resample_alg,
                        dstSRS=dest_crs.toWkt(),  # 대상 좌표계 재투영 강제 주입
                        callback=sub_callback
                    )
                    
                    if is_dem and dem_format == "Hillshade":
                        # 1. 임시 DEM 메모리 로드
                        warp_ds = gdal.Warp("/vsimem/export_dem.tif", source_path, options=warp_options)
                        
                        # 2. 다중방향 음영 연산 수행
                        is_multi = diag.cb_multidirectional.isChecked()
                        print(f"ℹ️ {target_layer.name()} 음영기복도 생성 시작 (고도: {diag.altitude}, 방위각: {diag.azimuth if not is_multi else 'N/A'}, Z척도: {diag.z_factor}, 다중방향: {is_multi})...")
                        scale_val = 1.0
                        if dest_crs.isGeographic():
                            scale_val = 111120.0
                            
                        opts_dict = {
                            "format": "GTiff",  # 파일로 최종 저장
                            "alg": "zevenbergenThorne",
                            "computeEdges": True,
                            "zFactor": diag.z_factor,
                            "scale": scale_val,
                            "altitude": diag.altitude
                        }
                        if is_multi:
                            opts_dict["multiDirectional"] = True
                        else:
                            opts_dict["azimuth"] = diag.azimuth
                            
                        dem_opts = gdal.DEMProcessingOptions(**opts_dict)
                        
                        # 파일 쓰기
                        ds_shade = gdal.DEMProcessing(out_path, warp_ds, "hillshade", options=dem_opts)
                        ds_shade = None  # 저장 완료
                        warp_ds = None   # 메모리 해제
                        gdal.Unlink("/vsimem/export_dem.tif")  # 가상 메모리 DEM 청소
                    else:
                        warp_ds = gdal.Warp(out_path, source_path, options=warp_options)
                        if warp_ds is not None:
                            warp_ds = None
                        
                    added_layer = iface.addRasterLayer(out_path, os.path.basename(out_path))
                    if added_layer and added_layer.isValid() and is_dem and dem_format == "Hillshade":
                        renderer = added_layer.renderer()
                        if renderer and renderer.type() == 'singlebandgray':
                            ce = renderer.contrastEnhancement()
                            if ce:
                                ce.setContrastEnhancementAlgorithm(ce.NoEnhancement)
                            added_layer.triggerRepaint()
                    success_count += 1
                    if is_dem:
                        exported_dem_path = out_path
                        
            except Exception as e:
                print(f"❌ {target_layer.name()} 크롭 실패: {str(e)}")

        self.progress_bar.setValue(100)
        self.progress_label.setText("일괄 크롭 마스터 완료!")
        QMessageBox.information(self, "일괄 저장 완료", f"총 {success_count}개의 지형 컴포넌트 레이어가 원터치 추출되었습니다.\n\n▶ 저장 폴더: {export_dir}")
        
        self.progress_bar.setValue(0)
        self.progress_label.setText("저장 대기 중...")

        if exported_dem_path and os.path.exists(exported_dem_path):
            ext = os.path.splitext(exported_dem_path)[1]
            reply = QMessageBox.question(
                self, "R16 변환 확인", 
                f"DEM ({ext}) 파일 추출이 완료되었습니다.\n크라이엔진용 R16 (.r16) 파일로 지금 바로 변환하시겠습니까?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self.open_r16_converter(exported_dem_path)

    def close_controller(self):
        if self.is_layer_valid() and self.layer.isEditable():
            self.layer.commitChanges()
        self.close()
        self.plugin_ref.reactivate_tool() 

    def open_r16_converter(self, default_input=""):
        if not isinstance(default_input, str):
            default_input = ""
        diag = R16ConversionDialog(parent=self, default_input=default_input)
        diag.exec_() 

class ClickToSquareColorPresetTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, plugin_ref):
        super(ClickToSquareColorPresetTool, self).__init__(canvas)
        self.canvas = canvas
        self.plugin_ref = plugin_ref
        self.canvasClicked.connect(self.handle_click)

    def handle_click(self, point, button):
        if button != 1: return
            
        dialog = MultiSizeSelectDialog(iface.mainWindow())
        if not dialog.exec_(): return

        selected_sizes = dialog.selected_sizes

        color_map = {
            "2048": QColor(0, 200, 0, 255),    
            "4096": QColor(255, 0, 0, 255),    
            "8192": QColor(0, 0, 255, 255),    
            "15360": QColor(128, 0, 128, 255) 
        }

        # 정렬하여 가장 큰 크기가 마지막에 생성되도록 (컨트롤러 활성 바인딩용)
        sorted_sizes = sorted(selected_sizes, key=int)
        
        last_created_layer = None
        last_size_label = None
        last_line_color = None

        crs_auth = self.canvas.mapSettings().destinationCrs().authid()
        cx, cy = point.x(), point.y()

        for size_str in sorted_sizes:
            distance_m = float(size_str)
            line_color = color_map.get(size_str, QColor(150, 0, 255, 255))
            size_label = size_str

            half_size = distance_m / 2.0
            rect = QgsRectangle(cx - half_size, cy - half_size, cx + half_size, cy + half_size)
            square_geo = QgsGeometry.fromRect(rect)
            
            temp_layer = QgsVectorLayer(f"Polygon?crs={crs_auth}", f"Temp_Square_{size_label}m", "memory")
            provider = temp_layer.dataProvider()
            
            feature = QgsFeature()
            feature.setGeometry(square_geo)
            provider.addFeatures([feature])
            
            symbol = temp_layer.renderer().symbol()
            stroke_symbol = QgsSimpleFillSymbolLayer()
            stroke_symbol.setFillColor(QColor(0, 0, 0, 0))
            stroke_symbol.setStrokeColor(line_color)
            stroke_symbol.setStrokeWidth(1.5)
            symbol.changeSymbolLayer(0, stroke_symbol)
            
            QgsProject.instance().addMapLayer(temp_layer)
            temp_layer.triggerRepaint()
            
            last_created_layer = temp_layer
            last_size_label = size_label
            last_line_color = line_color

        self.canvas.refresh()
        
        if last_created_layer:
            # Check if a controller is already open and visible
            active_controller = None
            for c in self.plugin_ref.controllers:
                try:
                    if c.isVisible():
                        active_controller = c
                        break
                except:
                    pass
                    
            if active_controller:
                active_controller.set_active_layer(last_created_layer, last_size_label, last_line_color)
                active_controller.refresh_layers() # 드롭다운 리스트 즉시 갱신
            else:
                controller = TerrainEditController(last_created_layer, last_size_label, last_line_color, self.plugin_ref)
                self.plugin_ref.controllers.append(controller)
            
        self.canvas.setMapTool(None)

class CryTerrainPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.tool = None
        self.controllers = []

    def initGui(self):
        self.action = QAction("📐 지형 거리별 크롭 박스 생성", self.iface.mainWindow())
        self.action.triggered.connect(self.run_tool)
        self.iface.addPluginToMenu("&지형 크롭 도구", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginMenu("&지형 크롭 도구", self.action)
            self.iface.removeToolBarIcon(self.action)
        for c in self.controllers:
            try: c.close()
            except: pass

    def run_tool(self):
        # Search for any vector layer whose name contains "CropBounds" or "Temp_Square"
        project = QgsProject.instance()
        crop_layer = None
        for layer in project.mapLayers().values():
            if isinstance(layer, QgsVectorLayer):
                lname = layer.name().lower()
                if "cropbounds" in lname or "temp_square" in lname:
                    crop_layer = layer
                    break
        
        size_label = "2048"
        line_color = QColor(0, 200, 0, 255)
        
        if crop_layer:
            name = crop_layer.name()
            if "m" in name:
                parts = name.split("_")
                for part in parts:
                    if part.endswith("m"):
                        size_label = part[:-1]
            try:
                symbol = crop_layer.renderer().symbol()
                line_color = symbol.symbolLayer(0).strokeColor()
            except:
                pass
        
        # Check if a controller is already open and visible
        active_controller = None
        for c in self.controllers:
            try:
                if c.isVisible():
                    active_controller = c
                    break
            except:
                pass
                
        if active_controller:
            if crop_layer:
                active_controller.set_active_layer(crop_layer, size_label, line_color)
            active_controller.raise_()
            active_controller.activateWindow()
        else:
            self.controllers = []
            controller = TerrainEditController(crop_layer, size_label, line_color, self)
            self.controllers.append(controller)

    def reactivate_tool(self):
        pass