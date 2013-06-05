import copy
import numpy
import vigra
from lazyflow.graph import Operator, InputSlot, OutputSlot
from lazyflow.roi import roiFromShape, roiToSlice, getIntersectingBlocks, getBlockBounds

from ilastik.workflows.carving.opCarving import OpCarving

import logging
logger = logging.getLogger(__name__)

class OpSplitBodyCarving( OpCarving ):
    
    RavelerLabels = InputSlot()
    CurrentRavelerLabel = InputSlot(value=0)
    
    HighlightedRavelerObject = OutputSlot()
    MaskedSegmentation = OutputSlot()

    BLOCK_SIZE = 520
    SEED_MARGIN = 10

    def __init__(self, *args, **kwargs):
        super( OpSplitBodyCarving, self ).__init__( *args, **kwargs )
        self._opHighlighter = OpHighlightLabel( parent=self )
        self._opHighlighter.HighlightLabel.connect( self.CurrentRavelerLabel )
        self._opHighlighter.Input.connect( self.RavelerLabels )
        self.HighlightedRavelerObject.connect( self._opHighlighter.Output )

    @classmethod
    def autoSeedBackground(cls, laneView, foreground_label):
        # Seed the entire image with background labels, except for the individual label in question
        # To save memory, we'll do this in blocks instead of all at once

        volume_shape = laneView.RavelerLabels.meta.shape
        volume_roi = roiFromShape( volume_shape )
        block_shape = (OpSplitBodyCarving.BLOCK_SIZE,) * len( volume_shape ) 
        block_shape = numpy.minimum( block_shape, volume_shape )
        block_starts = getIntersectingBlocks( block_shape, volume_roi )

        logger.debug("Auto-seeding {} blocks for label".format( len(block_starts), foreground_label ))
        for block_index, block_start in enumerate(block_starts):
            block_roi = getBlockBounds( volume_shape, block_shape, block_start )
            label_block = laneView.RavelerLabels(*block_roi).wait()
            background_block = numpy.where( label_block == foreground_label, 0, 1 )
            background_block = numpy.asarray( background_block, numpy.float32 ) # Distance transform requires float
            if (background_block == 0.0).any():
                # We need to leave a small border between the background seeds and the object membranes
                background_block_view = background_block.view( vigra.VigraArray )
                background_block_view.axistags = copy.copy( laneView.RavelerLabels.meta.axistags )
                
                background_block_view_4d = background_block_view.bindAxis('t', 0)
                background_block_view_3d = background_block_view_4d.bindAxis('c', 0)
                
                distance_transformed_block = vigra.filters.distanceTransform3D(background_block_view_3d, background=False)
                distance_transformed_block = distance_transformed_block.astype( numpy.uint8 )
                
                # Create a 'hull' surrounding the foreground, but leave some space.
                background_seed_block = (distance_transformed_block == OpSplitBodyCarving.SEED_MARGIN)
                background_seed_block = background_seed_block.astype(numpy.uint8) * 1 # (In carving, background is label 1)

#                # Make the hull VERY sparse to avoid over-biasing graph cut toward the background class
#                # FIXME: Don't regenerate this random block on every loop iteration
#                rand_bytes = numpy.random.randint(0, 1000, background_seed_block.shape)
#                background_seed_block = numpy.where( rand_bytes < 1, background_seed_block, 0 )
#                background_seed_block = background_seed_block.view(vigra.VigraArray)
#                background_seed_block.axistags = background_block_view_3d.axistags
                
                axisorder = laneView.RavelerLabels.meta.getTaggedShape().keys()
                
                logger.debug("Writing backgound seeds: {}/{}".format( block_index, len(block_starts) ))
                laneView.WriteSeeds[ roiToSlice( *block_roi ) ] = background_seed_block.withAxes(*axisorder)
            else:
                logger.debug("Skipping all-background block: {}/{}".format( block_index, len(block_starts) ))

    def setupOutputs(self):
        super( OpSplitBodyCarving, self ).setupOutputs()
        self.MaskedSegmentation.meta.assignFrom(self.Segmentation.meta)
        def handleDirtySegmentation(slot, roi):
            self.MaskedSegmentation.setDirty( roi )
        self.Segmentation.notifyDirty( handleDirtySegmentation )
    
    def execute(self, slot, subindex, roi, result):
        if slot == self.MaskedSegmentation:
            ravelerLabels = self.RavelerLabels(roi.start, roi.stop).wait()
            result = self.Segmentation(roi.start, roi.stop).writeInto(result).wait()
            result[:] = numpy.where(ravelerLabels == self.CurrentRavelerLabel.value, result, 0)
            return result
        else:
            return super( OpSplitBodyCarving, self ).execute( slot, subindex, roi, result )
    
        if self.HighlightLabel.value == 0:
            result[:] = 0
        else:
            self.Input(roi.start, roi.stop).writeInto(result).wait()
            result[:] = numpy.where( result == self.HighlightLabel.value, 1, 0 )
        return result
    
    def propagateDirty(self, slot, subindex, roi):
        if slot == self.RavelerLabels:
            self.MaskedSegmentation.setDirty( roi.start, roi.stop )
        elif slot == self.CurrentRavelerLabel:
            self.MaskedSegmentation.setDirty( slice(None) )
        else:
            return super( OpSplitBodyCarving, self ).propagateDirty( slot, subindex, roi )        

class OpHighlightLabel(Operator):
    Input = InputSlot()
    HighlightLabel = InputSlot()
    Output = OutputSlot()
    
    def __init__(self, *args, **kwargs):
        super( OpHighlightLabel, self ).__init__( *args, **kwargs )
    
    def setupOutputs(self):
        self.Output.meta.assignFrom(self.Input.meta)
    
    def execute(self, slot, subindex, roi, result):
        assert slot == self.Output, "Unknown output slot: {}".format( slot.name )
        if self.HighlightLabel.value == 0:
            result[:] = 0
        else:
            self.Input(roi.start, roi.stop).writeInto(result).wait()
            result[:] = numpy.where( result == self.HighlightLabel.value, 1, 0 )
        return result
    
    def propagateDirty(self, slot, subindex, roi):
        if slot == self.Input:
            self.Output.setDirty( roi.start, roi.stop )
        elif slot == self.HighlightLabel:
            self.Output.setDirty( slice(None) )
        else:
            assert False, "Dirty slot is unknown: {}".format( slot.name )



